from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Union, cast

from totelegram.core.schemas import AccessReport, AccessStatus, ChatMatch
from totelegram.core.setting import normalize_chat_id

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Chat, ChatMember

logger = logging.getLogger(__name__)


class ChatAccessService:
    def __init__(self, client: "Client"):
        self.client = client

    def _force_refresh_peers(self, depth: int = 100):
        """
        Recorre los diálogos recientes para forzar la actualización
        de la caché interna de peers (access_hash).

        Útil cuando Telegram no reconoce un chat aún presente
        en los diálogos del usuario.
        """
        try:
            for _ in self.client.get_dialogs(depth):  # type: ignore
                pass
        except Exception as e:
            logger.debug(f"Error refrescando peers: {e}")

    def _get_membership(self, chat: "Chat") -> Optional["ChatMember"]:
        """
        Obtiene el objeto ChatMember del usuario actual para el chat dado.

        Devuelve None si el chat es privado, el usuario no es miembro
        o no se puede determinar la membresía.
        """
        from pyrogram.enums import ChatType
        from pyrogram.errors import RPCError, UserNotParticipant

        if chat.type == ChatType.PRIVATE:
            return None

        # Nota:
        # Se esta tratando como 'no miembro' estos casos: Errror RCP, Fallo de telegram y problemas de red.
        # Dificil que ocurra, pero se contempla por las dudas.

        try:
            return self.client.get_chat_member(chat.id, "me")  # type: ignore
        except UserNotParticipant:
            return None
        except RPCError as e:
            logger.warning(f"Error verificando membresía en {chat.id}: {e}")
            return None

    def _can_interact_channel(self, chat: "Chat") -> bool:
        """
        Determina si el usuario puede publicar mensajes en un canal.

        Solo propietarios o administradores con permisos explícitos
        de publicación pueden interactuar.
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

    def _can_interact_group(self, chat: "Chat") -> bool:
        """
        Evalúa si el usuario puede enviar mensajes en un grupo o supergrupo.

        Considera el estado del miembro y las restricciones
        definidas tanto a nivel de chat como individuales.
        """
        from pyrogram.enums import ChatMemberStatus

        member = self._get_membership(chat)
        if member is None:
            return False

        if member.status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
        ):
            return True

        if member.status == ChatMemberStatus.MEMBER:
            return bool(chat.permissions and chat.permissions.can_send_media_messages)

        if member.status == ChatMemberStatus.RESTRICTED:
            return bool(
                member.permissions and member.permissions.can_send_media_messages
            )

        return False

    def can_interact(self, chat: "Chat") -> bool:
        """
        Determina si el usuario actual puede enviar mensajes
        en el chat especificado.

        Aplica reglas distintas según el tipo de chat.
        """
        from pyrogram.enums import ChatType

        if chat.type == ChatType.PRIVATE:
            return True

        if chat.type == ChatType.CHANNEL:
            return self._can_interact_channel(chat)

        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            return self._can_interact_group(chat)

        return False

    def send_action(self, chat: "Chat") -> bool:
        """
        Intenta enviar una acción de chat (por ejemplo, 'typing')
        para verificar capacidad básica de escritura.

        Devuelve True si la acción se envía correctamente.
        """

        # TODO: Analizar si esto es mejor que usar `self.can_interact`

        from pyrogram.enums import ChatAction

        try:
            self.client.send_chat_action(chat.id, ChatAction.TYPING)  # type: ignore
            return True
        except ValueError:
            return False

    def get_chat(self, chat_id: Union[str, int]) -> Optional["Chat"]:
        """
        Obtiene el objeto Chat correspondiente al identificador dado.

        Si el chat no está en caché, intenta refrescar los diálogos
        antes de devolver None.
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
            # TODO: ¿Deberiamos soportar estos errores?
            pass

        logger.error(f"Error: No se encuentra el chat '{chat_id}'.")
        return None

    def verify_access(self, query: Union[str, int]) -> AccessReport:
        """
        Evalúa el acceso del usuario a un destino en tres etapas:

        - existencia del chat
        - membresía
        - permisos de interacción.

        Devuelve un AccessReport con el resultado del análisis.
        """

        normalized_id = normalize_chat_id(query)
        chat = self.get_chat(normalized_id)
        if not chat:
            return AccessReport(
                status=AccessStatus.NOT_FOUND,
                reason=f"Telegram no reconoce el destino '{query}'.",
                hint="Tip: Si es un ID, asegúrate de haber interactuado con él. Si no, busca por nombre con 'config resolve'.",
            )

        from pyrogram.enums import ChatType

        if chat.type != ChatType.PRIVATE:
            # Nota:
            # _get_membership puede ser llamado más de una vez durante el flujo.
            # Asumo el costo a cambio de un código más legible.
            if not self._get_membership(chat):
                return AccessReport(
                    status=AccessStatus.NOT_MEMBER,
                    chat=ChatMatch.from_chat(chat),
                    reason=f"Encontré '{chat.title}', pero no eres miembro.",
                    hint="Tip: Únete al grupo/canal en tu app de Telegram y vuelve a intentarlo.",
                )

            if not self.can_interact(chat):
                return AccessReport(
                    status=AccessStatus.RESTRICTED,
                    chat=ChatMatch.from_chat(chat),
                    reason=f"Eres miembro de '{chat.title}', pero no tienes permiso para enviar archivos.",
                    hint="Tip: Solicita a un administrador que te otorgue permisos de escritura.",
                )

        return AccessReport(
            status=AccessStatus.READY,
            chat=ChatMatch.from_chat(chat),
            reason="Acceso verificado correctamente.",
        )
