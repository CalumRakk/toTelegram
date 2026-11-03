from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, cast

from totelegram.telegram.types import AccessReport, AccessStatus, ChatMatch
from totelegram.telegram.utils import normalize_chat_id

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Chat, ChatMember

logger = logging.getLogger(__name__)


class ChatAccessService:
    def __init__(self, client: "Client"):
        self.client = client

    def _force_refresh_peers(self, depth: int = 100):
        """
        Recorre los diálogos recientes para forzar la actualización
        de la caché interna de peers (access_hash).
        """
        try:
            for _ in self.client.get_dialogs(depth):  # type: ignore
                pass
        except Exception as e:
            logger.debug(f"Error refrescando peers: {e}")

    def _is_self_chat(self, chat: "Chat") -> bool:
        """Determina si un chat corresponde inequívocamente a Mensajes Guardados ('me')."""
        from pyrogram.enums import ChatType

        if chat.type != ChatType.PRIVATE:
            return False

        me = getattr(self.client, "me", None)
        if me and chat.id == me.id:
            return True

        try:
            my_info = self.client.get_me()
            return bool(my_info and chat.id == my_info.id)
        except Exception:
            return False

    def _get_membership(self, chat: "Chat") -> Optional["ChatMember"]:  # noqa: UP045
        """
        Obtiene el objeto ChatMember del usuario actual para el chat dado.
        Devuelve None si el chat es privado, el usuario no es miembro
        o no se puede determinar la membresía.
        """
        from pyrogram.enums import ChatType
        from pyrogram.errors import RPCError, UserNotParticipant

        if chat.type == ChatType.PRIVATE:
            return None

        try:
            return self.client.get_chat_member(chat.id, "me")
        except UserNotParticipant:
            return None
        except RPCError as e:
            logger.warning(f"Error verificando membresía en {chat.id}: {e}")
            return None

    def _can_interact_channel(self, chat: "Chat") -> bool:
        """
        Determina si el usuario puede publicar mensajes en un canal.
        Solo propietarios o administradores con permisos explícitos de publicación pueden interactuar.
        """
        from pyrogram.enums import ChatMemberStatus

        member = self._get_membership(chat)
        if member is None:
            return False

        if member.status == ChatMemberStatus.OWNER:
            return True

        if member.status == ChatMemberStatus.ADMINISTRATOR:
            return bool(member.privileges and member.privileges.can_post_messages)

        return False

    def can_interact(self, chat: "Chat") -> bool:
        """
        Determina si el usuario actual puede enviar mensajes en el chat especificado.
        Solo es válido para Mensajes Guardados ('me') o Canales con permisos de publicación.
        """
        from pyrogram.enums import ChatType

        if self._is_self_chat(chat):
            return True

        if chat.type == ChatType.CHANNEL:
            return self._can_interact_channel(chat)

        return False

    def get_chat(self, chat_id: str | int) -> Optional["Chat"]:  # noqa: UP045
        """
        Obtiene el objeto Chat correspondiente al identificador dado.
        Si el chat no está en caché, intenta refrescar los diálogos antes de fallar.
        """
        from pyrogram.errors import ChannelPrivate, PeerIdInvalid, UsernameInvalid
        from pyrogram.types import Chat

        try:
            logger.debug(f"Buscando chat '{chat_id}'...")
            return cast(Chat, self.client.get_chat(chat_id))
        except PeerIdInvalid:
            try:
                logger.warning("Chat no en caché, escaneando diálogos...")
                self._force_refresh_peers()
                chat = cast(Chat, self.client.get_chat(chat_id))
                logger.info(f"Chat encontrado: {chat.title} (ID: {chat.id})")
                return chat
            except PeerIdInvalid:
                pass
        except (UsernameInvalid, ChannelPrivate):
            pass

        logger.error(f"Error: No se encuentra el chat '{chat_id}'.")
        return None

    def verify_access(self, query: str | int) -> AccessReport:
        """
        Evalúa el acceso del usuario a un destino validando:
        1. Existencia del chat.
        2. Compatibilidad del tipo de destino (solo Canales o 'me').
        3. Membresía y permisos de administrador en caso de ser canal.
        """
        logger.info(f"Verificando permisos de acceso para el destino: {query}")
        normalized_id = normalize_chat_id(query)
        chat = self.get_chat(normalized_id)
        if not chat:
            return AccessReport(
                status=AccessStatus.NOT_FOUND,
                reason=f"Telegram no reconoce el destino '{query}'.",
                hint="Tip: Si es un ID, asegúrate de haber interactuado con él. Si no, busca por nombre con 'totelegram target'.",
            )

        from pyrogram.enums import ChatType

        # Mensajes Guardados ('me'): Siempre permitido
        if self._is_self_chat(chat):
            return AccessReport(
                status=AccessStatus.READY,
                chat=ChatMatch.from_chat(chat),
                reason="Acceso verificado a Mensajes Guardados ('me').",
            )

        # Rechazar chats privados ajenos o bots
        if chat.type in (ChatType.PRIVATE, ChatType.BOT):
            title = chat.first_name or chat.title or str(chat.id)
            return AccessReport(
                status=AccessStatus.UNSUPPORTED_TYPE,
                chat=ChatMatch.from_chat(chat),
                reason=f"'{title}' es un chat privado ajeno o bot.",
                hint="Tip: toTelegram solo admite Canales de difusión o tu propio chat de Mensajes Guardados ('me').",
            )

        # Rechazar grupos y supergrupos
        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            chat_kind = "Supergrupo" if chat.type == ChatType.SUPERGROUP else "Grupo"
            return AccessReport(
                status=AccessStatus.UNSUPPORTED_TYPE,
                chat=ChatMatch.from_chat(chat),
                reason=f"'{chat.title}' es un {chat_kind}. toTelegram no admite grupos para almacenamiento.",
                hint="Tip: Los grupos no permiten edición cruzada de metadatos ni reorganización limpia. Crea un Canal privado y úsalo como destino.",
            )

        # En este punto garantizamos que es ChatType.CHANNEL
        member = self._get_membership(chat)
        if not member:
            return AccessReport(
                status=AccessStatus.NOT_MEMBER,
                chat=ChatMatch.from_chat(chat),
                reason=f"Encontré el canal '{chat.title}', pero no eres miembro.",
                hint="Tip: Únete al canal en tu app de Telegram, hazte Administrador y vuelve a intentarlo.",
            )

        if not self._can_interact_channel(chat):
            logger.error(
                f"Permisos insuficientes: El usuario no puede publicar mensajes en el canal '{chat.title}'"
            )
            return AccessReport(
                status=AccessStatus.RESTRICTED,
                chat=ChatMatch.from_chat(chat),
                reason=f"Eres miembro del canal '{chat.title}', pero careces de permisos de administrador para publicar mensajes.",
                hint="Tip: Otorga a tu cuenta el permiso de administrador 'Publicar mensajes' (can_post_messages) en los ajustes del canal.",
            )

        return AccessReport(
            status=AccessStatus.READY,
            chat=ChatMatch.from_chat(chat),
            reason="Acceso y permisos de canal verificados correctamente.",
        )

    def can_delete_messages(self, chat_or_id: "Chat | int | str") -> bool:
        """
        Determina si el usuario actual tiene permisos para eliminar mensajes en el chat.
        - En Mensajes Guardados ('me'): Siempre True.
        - En canales: Solo Propietarios o Administradores con el privilegio 'can_delete_messages'.
        - En cualquier otro tipo de chat: False.
        """
        from pyrogram.enums import ChatMemberStatus, ChatType
        from pyrogram.types import Chat

        chat = chat_or_id if isinstance(chat_or_id, Chat) else self.get_chat(chat_or_id)
        if not chat:
            return False

        # En chats privados (incluido 'me') siempre se permite borrar
        if self._is_self_chat(chat) or chat.type == ChatType.PRIVATE:
            return True

        if chat.type != ChatType.CHANNEL:
            return False

        member = self._get_membership(chat)
        if member is None:
            return False

        if member.status == ChatMemberStatus.OWNER:
            return True

        if member.status == ChatMemberStatus.ADMINISTRATOR:
            return bool(member.privileges and member.privileges.can_delete_messages)

        return False
