import logging
from typing import Optional, Union, cast

from pyrogram import Client, enums  # type: ignore
from pyrogram.errors import (
    AccessTokenInvalid,
    ApiIdInvalid,
    ApiIdPublishedFlood,
    ChannelPrivate,
    ChatWriteForbidden,
    PeerIdInvalid,
    UsernameInvalid,
)
from pyrogram.types import Chat
from rich.console import Console

from totelegram.core.setting import get_user_config_dir

logger = logging.getLogger(__name__)


class ValidationService:
    """
    Servicio dedicado exclusivamente a validar credenciales y permisos
    antes de guardar un perfil.
    """

    def __init__(self, console: Console):
        self.console = console

    def validate_setup(
        self, profile_name: str, api_id: int, api_hash: str, target_chat_id: str
    ) -> bool:
        """Flujo principal de validación."""

        workdir = get_user_config_dir("toTelegram") / "profiles"
        workdir.mkdir(parents=True, exist_ok=True)

        self.console.print(
            f"\n[bold blue]Iniciando validación para '{profile_name}'...[/bold blue]"
        )

        client = Client(
            name=profile_name,
            api_id=api_id,
            api_hash=api_hash,
            workdir=str(workdir),
            in_memory=False,
        )

        try:
            self.console.print("[yellow]Conectando con Telegram...[/yellow]")
            client.start()  # type: ignore

            # Validar Login
            me = cast(Chat, client.get_me())
            self.console.print(
                f"[green]✔ Login exitoso como:[/green] {me.first_name} (@{me.username})"
            )

            # Resolver Chat
            chat = self._resolve_target_chat(client, target_chat_id)
            if not chat:
                return False

            # Validar Permisos de Escritura
            if not self._verify_permissions(client, chat):
                self.console.print(
                    "[bold red]✘ Error de Permisos:[/bold red] No puedes enviar archivos a este chat."
                )
                return False

            self.console.print(
                f"[bold green]✔ Validación completada. Todo listo.[/bold green]"
            )
            return True

        except (ApiIdInvalid, AccessTokenInvalid):
            self.console.print("[bold red]✘ Error:[/bold red] API ID o Hash inválidos.")
        except ApiIdPublishedFlood:
            self.console.print(
                "[bold red]✘ Error:[/bold red] API ID baneado públicamente."
            )
        except Exception as e:
            self.console.print(f"[bold red]✘ Error inesperado:[/bold red] {e}")
            logger.exception("Error en validación")
        finally:
            if client.is_connected:
                client.stop()  # type: ignore

        return False

    def _resolve_target_chat(self, client, chat_id: Union[str, int]) -> Optional[Chat]:
        """Intenta obtener el chat, refrescando peers si es necesario."""
        self.console.print(f"[yellow]Buscando chat '{chat_id}'...[/yellow]")

        try:
            return cast(Chat, client.get_chat(chat_id))
        except PeerIdInvalid:
            self.console.print("[dim]Chat no en caché, escaneando diálogos...[/dim]")
            self._force_refresh_peers(client)
            try:
                chat = cast(Chat, client.get_chat(chat_id))
                self.console.print(
                    f"[green]✔ Chat encontrado:[/green] {chat.title} (ID: {chat.id})"
                )
                return chat
            except PeerIdInvalid:
                pass
        except (UsernameInvalid, ChannelPrivate):
            pass

        self.console.print(
            f"[bold red]✘ Error:[/bold red] No se encuentra el chat '{chat_id}'."
        )
        return None

    def _force_refresh_peers(self, client):
        """Recorre diálogos para poblar caché de access_hash."""
        try:
            count = 0
            for _ in client.get_dialogs(limit=200):
                count += 1
        except Exception:
            pass

    def _verify_permissions(self, client, chat: Chat) -> bool:
        """
        Verifica si 'me' tiene permisos para enviar media en el chat.
        """
        self.console.print(
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
                    self.console.print(
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
