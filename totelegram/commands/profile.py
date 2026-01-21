import uuid
from typing import Optional

import typer

from totelegram.commands.profile_ui import ProfileUI
from totelegram.commands.profile_utils import (
    _finalize_profile,
    _suggest_profile_activation,
    _validate_chat_with_retry,
    validate_profile_name,
)
from totelegram.console import console
from totelegram.core.registry import ProfileManager
from totelegram.services.validator import ValidationService
from totelegram.store.database import DatabaseSession
from totelegram.store.models import TelegramChat

app = typer.Typer(help="Gestión de perfiles de configuración.")
pm = ProfileManager()
ui = ProfileUI(console)


@app.command("create")
def create_profile(
    profile_name: str = typer.Option(
        ..., help="Nombre del perfil", prompt=True, callback=validate_profile_name
    ),
    api_id: int = typer.Option(..., help="API ID", prompt=True),
    api_hash: str = typer.Option(..., help="API Hash", prompt=True, hide_input=True),
    chat_id: str = typer.Option(..., help="Chat ID o Username", prompt=True),
):
    """Crea un nuevo perfil de configuración interactivamente."""

    final_session = ProfileManager.PROFILES_DIR / f"{profile_name}.session"
    temp_name = f"temp_{uuid.uuid4().hex[:8]}"
    temp_session = ProfileManager.PROFILES_DIR / f"{temp_name}.session"

    validator = ValidationService()
    try:
        with validator.validate_session(temp_name, api_id, api_hash) as client:
            if not _validate_chat_with_retry(validator, client, chat_id):
                if temp_session.exists():
                    temp_session.unlink()
                raise typer.Exit(0)
    except Exception as e:
        if temp_session.exists():
            temp_session.unlink()
        raise e

    _finalize_profile(
        profile_name, temp_session, final_session, api_id, api_hash, chat_id
    )
    _suggest_profile_activation(profile_name)


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
        console.print(
            f"[bold green]✔ Ahora usando el perfil: {profile_name}[/bold green]"
        )
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        list_profiles(quiet=True)


@app.command("list")
def list_profiles(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Salida silenciosa")
):
    """Enumera todos los perfiles registrados."""
    registry = pm.get_registry()
    if not registry.profiles:
        console.print("[yellow]No hay perfiles registrados.[/yellow]")
        console.print(
            "Usa [yellow]'totelegram profile create'[/yellow] para crear uno nuevo."
        )
        return
    ui.render_profiles_table(registry.active, registry.profiles, quiet)


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
        console.print(f"[green]Perfil '{name}' eliminado.[/green]")
