from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Dict, Generator, List, Optional, Union, cast

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
        from pyrogram.types import Chat

        ProfileManager.PROFILES_DIR.mkdir(parents=True, exist_ok=True)

        console.print(
            f"\n[bold blue]Iniciando validación de credenciales...[/bold blue]"
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

    def validate_chat_id(
        self, client: Client, target_chat_id: str | int
    ) -> Optional["Chat"]:
        """
        Retorna el objeto Chat si lo encuentra, de lo contrario None.
        """
        chat = self._resolve_target_chat(client, target_chat_id)
        if not chat:
            console.print(
                f"[bold red]✘ Error:[/bold red] No se encuentra el chat '{target_chat_id}'."
            )
            return None

        # Informamos sobre permisos
        if not self._verify_permissions(client, chat):
            console.print(
                "[bold yellow]⚠ Advertencia de Permisos:[/bold yellow] "
                "Parece que no tienes permisos de escritura en este chat. "
                "Podrás guardarlo, pero las subidas podrían fallar."
            )
        else:
            console.print(
                f"[bold green]✔ Chat verificado:[/bold green] {chat.title or 'Privado'}"
            )

        return chat

    def _resolve_target_chat(
        self, client, chat_id: Union[str, int]
    ) -> Optional["Chat"]:
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

    def _force_refresh_peers(self, client: "Client"):
        """Recorre diálogos para poblar caché de access_hash."""
        try:
            count = 0
            for _ in client.get_dialogs():  # type: ignore
                count += 1
        except Exception:
            pass

    def _verify_permissions(self, client: "Client", chat: "Chat") -> bool:
        """
        Verifica la session del client tiene permisos para escribir en el chat.
        """
        from pyrogram import enums
        from pyrogram.errors import ChatWriteForbidden
        from pyrogram.types import ChatMember

        console.print(
            f"[dim]Verificando permisos de escritura en {chat.type.value}...[/dim]"
        )

        # Chat Privado (Mensajes guardados o DM)
        if chat.type == enums.ChatType.PRIVATE:
            return True

        try:
            member: ChatMember = client.get_chat_member(chat.id, "me")  # type: ignore

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

    def search_chats(self, client: Client, query: str) -> List[Dict]:
        """Busca en los diálogos recientes del usuario."""
        results = []
        with console.status(f"[dim]Buscando '{query}' en tus chats...[/dim]"):
            for dialog in client.get_dialogs(limit=50):  # type: ignore
                chat = dialog.chat
                title = (
                    chat.title
                    or f"{chat.first_name or ''} {chat.last_name or ''}".strip()
                )

                # Filtro simple por nombre o username
                if query.lower() in title.lower() or (
                    chat.username and query.lower() in chat.username.lower()
                ):
                    results.append(
                        {
                            "id": chat.id,
                            "title": title,
                            "type": chat.type.value,
                            "username": chat.username,
                        }
                    )

                if len(results) >= 10:
                    break
        return results
