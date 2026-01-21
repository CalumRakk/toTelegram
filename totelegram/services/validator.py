from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator, Optional, Union, cast

from totelegram.console import console
from totelegram.core.registry import ProfileManager
from totelegram.telegram import TelegramSession

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram import enums
    from pyrogram.errors import (
        ApiIdInvalid,
        ApiIdPublishedFlood,
        ChannelPrivate,
        ChatWriteForbidden,
        PeerIdInvalid,
        UsernameInvalid,
    )
    from pyrogram.types import Chat

logger = logging.getLogger(__name__)


# TODO: nombre de clase demasiado generico, deberia incluir "Telegram" o algo similar.
class ValidationService:
    """
    Servicio dedicado exclusivamente a validar credenciales y permisos
    antes de guardar un perfil.
    """

    @contextmanager
    def validate_session(
        self, profile_name: str, api_id: int, api_hash: str
    ) -> Generator[Client, None, None]:
        from pyrogram.errors import ApiIdInvalid, ApiIdPublishedFlood

        ProfileManager.PROFILES_DIR.mkdir(parents=True, exist_ok=True)

        console.print(
            f"\n[bold blue]Iniciando validación para '{profile_name}'...[/bold blue]"
        )
        try:
            with TelegramSession(
                session_name=profile_name,
                api_id=api_id,
                api_hash=api_hash,
                workdir=ProfileManager.PROFILES_DIR,
            ) as client:

                me = cast(Chat, client.get_me())
                console.print(
                    f"[green]✔ Login exitoso como:[/green] {me.first_name} (@{me.username})"
                )
                yield client

        except ApiIdInvalid as e:
            logger.debug("Error: API ID o Hash inválidos.")
            raise e
        except ApiIdPublishedFlood as e:
            logger.debug("Error: API ID baneado públicamente.")
            raise e
        except Exception as e:
            logger.debug(f"Error: inesperado: {e}")
            raise e

    def validate_chat_id(self, client: Client, target_chat_id: str) -> bool:
        """Valida que el chat ID o username sea accesible."""
        console.print(f"[yellow]Buscando chat '{target_chat_id}'...[/yellow]")

        chat = self._resolve_target_chat(client, target_chat_id)
        if not chat:
            return False

        if not self._verify_permissions(client, chat):
            console.print(
                "[bold red]✘ Error de Permisos:[/bold red] No puedes enviar archivos a este chat."
            )
            return False

        console.print(f"[bold green]✔ Validación completada. Todo listo.[/bold green]")
        return True

    def _resolve_target_chat(self, client, chat_id: Union[str, int]) -> Optional[Chat]:
        """Intenta obtener el chat, refrescando peers si es necesario."""
        from pyrogram.errors import ChannelPrivate, PeerIdInvalid, UsernameInvalid
        from pyrogram.types import Chat

        console.print(f"[yellow]Buscando chat '{chat_id}'...[/yellow]")

        try:
            return cast(Chat, client.get_chat(chat_id))
        except PeerIdInvalid:
            console.print("[dim]Chat no en caché, escaneando diálogos...[/dim]")
            self._force_refresh_peers(client)
            try:
                chat = cast(Chat, client.get_chat(chat_id))
                console.print(
                    f"[green]✔ Chat encontrado:[/green] {chat.title} (ID: {chat.id})"
                )
                return chat
            except PeerIdInvalid:
                pass
        except (UsernameInvalid, ChannelPrivate):
            pass

        console.print(
            f"[bold red]✘ Error:[/bold red] No se encuentra el chat '{chat_id}'."
        )
        return None

    def _force_refresh_peers(self, client):
        """Recorre diálogos para poblar caché de access_hash."""
        try:
            count = 0
            for _ in client.get_dialogs(limit=30):
                count += 1
        except Exception:
            pass

    def _verify_permissions(self, client, chat: Chat) -> bool:
        """
        Verifica si 'me' tiene permisos para enviar media en el chat.
        """
        from pyrogram import enums
        from pyrogram.errors import ChatWriteForbidden

        console.print(
            f"[dim]Verificando permisos de escritura en {chat.type.value}...[/dim]"
        )

        # Chat Privado (Mensajes guardados o DM)
        if chat.type == enums.ChatType.PRIVATE:
            return True

        try:
            member = client.get_chat_member(chat.id, "me")

            # Dueño o Admin
            if member.status in [
                enums.ChatMemberStatus.OWNER,
                enums.ChatMemberStatus.ADMINISTRATOR,
            ]:
                if chat.type == enums.ChatType.CHANNEL:
                    # En canales, solo admins con permiso de postear
                    return (
                        member.privileges.can_post_messages
                        if member.privileges
                        else True
                    )
                return True  # En grupos, los admins suelen poder mandar todo

            # Miembro normal
            if member.status == enums.ChatMemberStatus.MEMBER:
                if chat.type == enums.ChatType.CHANNEL:
                    console.print(
                        "[red]Los miembros no pueden escribir en canales.[/red]"
                    )
                    return False

                # En grupos, verificar restricciones globales o del chat
                if chat.permissions:
                    return chat.permissions.can_send_media_messages
                return True

            # Restringido
            if member.status == enums.ChatMemberStatus.RESTRICTED:
                return member.permissions.can_send_media_messages

        except ChatWriteForbidden:
            return False
        except Exception as e:
            logger.warning(f"No se pudieron verificar permisos exactos: {e}")
            return False

        return False
