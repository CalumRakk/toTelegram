import uuid
from typing import Optional

import typer
from rich.rule import Rule

from totelegram.commands.config import resolve_and_store_chat_logic
from totelegram.commands.profile_ui import ProfileUI
from totelegram.commands.profile_utils import (
    _capture_chat_id_wizard,
    suggest_profile_activation,
    validate_profile_name,
)
from totelegram.console import UI, console
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import CHAT_ID_NOT_SET
from totelegram.services.validator import ValidationService
from totelegram.store.database import DatabaseSession
from totelegram.store.models import TelegramChat
from totelegram.telegram import TelegramSession

app = typer.Typer(help="Gestión de perfiles de configuración.")
pm = ProfileManager()
ui = ProfileUI(console)


# @app.callback()
# def profile_main():
#     """Gestión de perfiles (este callback corre antes que create/list/etc)"""
#     warn_if_override_active()


@app.callback(invoke_without_command=True)
def profile_profile(ctx: typer.Context):
    """Muestra la lista de perfiles si no se pasa un subcomando."""
    if ctx.invoked_subcommand is not None:
        return
    list_profiles(False)


@app.command("list")
def list_profiles(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Salida silenciosa")
):
    """Muestra la lista de perfiles si no se pasa un subcomando."""
    registry = pm.get_registry()
    if not registry.profiles:
        UI.warn("No hay perfiles registrados.")
        UI.info(
            "Usa [yellow]'totelegram profile create'[/yellow] para crear uno nuevo."
        )
        return
    ui.render_profiles_table(registry.active, registry.profiles, quiet)


@app.command("create")
def create_profile(
    profile_name: str = typer.Option(..., prompt=True, callback=validate_profile_name),
    api_id: int = typer.Option(..., prompt=True),
    api_hash: str = typer.Option(..., prompt=True, hide_input=True),
    chat_id: Optional[str] = typer.Option(
        None, help="Chat ID (opcional para saltar asistente)"
    ),
):

    final_session = ProfileManager.PROFILES_DIR / f"{profile_name}.session"
    temp_name = f"temp_{uuid.uuid4().hex[:8]}"
    temp_session = ProfileManager.PROFILES_DIR / f"{temp_name}.session"

    validator = ValidationService()

    try:
        UI.info("\n[bold cyan]1. Autenticación con Telegram[/bold cyan]")
        UI.info(
            "[dim]Se solicitará tu número y código (OTP) para vincular la cuenta.[/dim]\n"
        )
        with validator.validate_session(temp_name, api_id, api_hash) as _:
            # Una vez dentro ya no necesitamos la session temp, salimos para liberar el archivo
            pass

        if not temp_session.exists():
            raise FileNotFoundError("Error crítico: No se generó el archivo de sesión.")

        # Por si habia una session previa, por alguna razon creativa del usuario.
        if final_session.exists():
            final_session.unlink()

        temp_session.rename(final_session)
        pm.create(
            name=profile_name, api_id=api_id, api_hash=api_hash, chat_id=CHAT_ID_NOT_SET
        )
        UI.success(
            f"[green]✔ Identidad salvada correctamente en {profile_name}.session[/green]"
        )

        # RESOLUCIÓN DE DESTINO
        console.print(Rule(style="dim"))
        UI.info("[bold cyan]2. Configuración del Destino[/bold cyan]")

        resolved_chat = chat_id

        settings = pm.get_settings(profile_name)

        with TelegramSession(settings) as client:
            if not resolved_chat:
                resolved_chat = _capture_chat_id_wizard(validator, client)

            if resolved_chat and resolved_chat != "me":
                success = resolve_and_store_chat_logic(resolved_chat, profile_name)
                if not success:
                    resolved_chat = CHAT_ID_NOT_SET
            elif resolved_chat == "me":
                pm.update_config("CHAT_ID", "me", profile_name=profile_name)
                UI.success("[green]✔ Destino configurado: Mensajes Guardados[/green]")

        console.print(Rule(style="dim"))
        if resolved_chat != CHAT_ID_NOT_SET:
            UI.success(
                f"[bold green]¡Éxito! Perfil '{profile_name}' listo para usar.[/bold green]"
            )
        else:
            UI.warn(
                f"[bold yellow]Perfil '{profile_name}' creado, pero sin destino configurado.[/bold yellow]"
            )
            UI.info("[dim]Puedes configurarlo luego usando:[/dim]")
            UI.info(f"  [cyan]totelegram config set chat_id <id>[/cyan]\n")

        suggest_profile_activation(profile_name)

    except Exception as e:
        console.print(f"\n[bold red]Error en la creación:[/bold red] {e}")
        raise typer.Exit(1)


@app.command("switch")
def use_profile(
    profile_name: str = typer.Argument(
        None, help="Nombre del perfil a activar globalmente", metavar="PERFIL"
    )
):
    """Cambia el perfil activo."""
    pm.get_registry()  # se usa para invocar a `_sync_registry_with_filesystem` y tener todo actualizado.

    if not profile_name:
        raise typer.BadParameter(
            "Debes indicar un perfil. Ejemplo: totelegram profile switch <PROFILE-NAME>"
        )

    try:
        pm.activate(profile_name)
        UI.success(f"Ahora usando el perfil: [bold]{profile_name}[/]")
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        list_profiles(quiet=True)


def get_chat_name(current_settings) -> Optional[str]:

    with DatabaseSession(current_settings):
        target = current_settings.chat_id
        chat = None
        try:
            chat = TelegramChat.get_or_none(TelegramChat.id == int(target))
        except (ValueError, TypeError):
            clean_username = str(target).replace("@", "")
            chat = TelegramChat.get_or_none(TelegramChat.username == clean_username)

        if chat:
            return chat.title


@app.command("delete")
def delete_profile(name: str):
    """Borra un perfil (archivo .env y .session)."""
    try:
        pm.resolve_name(name)
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        list_profiles(quiet=True)
        raise typer.Exit(code=1)

    if typer.confirm(
        f"¿Estás seguro de que deseas borrar el perfil '{name}' y su configuración?"
    ):
        pm.delete_profile(name)
        UI.success(f"Perfil '[bold]{name}[/]' eliminado.")
