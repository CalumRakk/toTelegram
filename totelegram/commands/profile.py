import uuid
from typing import List, Optional

import typer
from pydantic import ValidationError

from totelegram.commands.profile_ui import ProfileUI
from totelegram.commands.profile_utils import (
    _finalize_profile,
    _handle_list_operation,
    _suggest_profile_activation,
    _validate_chat_with_retry,
    validate_profile_name,
)
from totelegram.console import console
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import Settings, get_settings
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


@app.command("set", context_settings={"allow_interspersed_args": False})
def set_config(
    key: str = typer.Argument(..., help="Clave a modificar"),
    value: str = typer.Argument(..., help="Nuevo valor"),
):
    """Edita una configuración."""
    try:
        profile_name = pm.resolve_name()
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        list_profiles(quiet=True)
        raise typer.Exit(code=1)

    ui.announce_profile_used(profile_name)
    try:
        validated_val = pm.update_config(key, value, profile_name=profile_name)
        if key.upper() == "CHAT_ID":
            _try_resolve_and_store_chat(profile_name, validated_val)

        checkmark_text = "[bold green]✔[/bold green]"
        console.print(
            f"{checkmark_text} La configuracion [bold]{key.upper()}[/bold] se establecio en: '{value}'."
        )
    except (ValidationError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


def _try_resolve_and_store_chat(profile_name: str, chat_id: str):
    """
    Intenta conectar a Telegram para obtener el nombre del chat
    y guardarlo en la DB local.
    """
    path = pm.get_path(profile_name)
    settings = get_settings(path)
    session_file = pm.PROFILES_DIR / f"{profile_name}.session"

    # Si no hay sesión, no podemos resolver nada aún
    if not session_file.exists():
        console.print(
            "[dim yellow]Nota: No hay archivo de sesión activo. El nombre del chat se resolverá en la próxima subida.[/dim yellow]"
        )
        return

    console.print(f"[dim]Resolviendo identidad del chat '{chat_id}'...[/dim]")

    from totelegram.services.validator import ValidationService
    from totelegram.store.database import DatabaseSession
    from totelegram.telegram import TelegramSession

    with DatabaseSession(settings):
        with TelegramSession(settings) as client:
            validator = ValidationService()
            chat_obj = validator._resolve_target_chat(client, chat_id)
            if chat_obj:
                TelegramChat.get_or_create_from_tg(chat_obj)
                console.print(
                    f"[green]✔ Chat detectado: [bold]{chat_obj.title}[/bold][/green]"
                )
                has_permissions = validator._verify_permissions(client, chat_obj)
                if has_permissions:
                    console.print(
                        "[green]✔ El Perfil tiene permisos para enviar media.[/green]"
                    )
                else:
                    console.print(
                        "[bold red]✘ El Perfil no tiene permisos para enviar media.[/bold red]"
                    )


@app.command("add")
def add_to_list(key: str, values: List[str], force: bool = False):
    """Agrega valores a una configuración de lista."""
    try:
        profile_name = pm.resolve_name()
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        list_profiles(quiet=True)
        raise typer.Exit(code=1)
    ui.announce_profile_used(profile_name)
    _handle_list_operation("add", key, values, profile_name, force)


@app.command("remove")
def remove_from_list(key: str, values: List[str], force: bool = False):
    """Elimina valores de una configuración de lista."""
    try:
        profile_name = pm.resolve_name()
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        list_profiles(quiet=True)
        raise typer.Exit(code=1)

    ui.announce_profile_used(profile_name)
    _handle_list_operation("remove", key, values, profile_name, force)


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


@app.command("options")
def list_options():
    """Lista las opciones de configuración y sus valores actuales."""
    schema = Settings.get_schema_info()
    current_settings = None
    active_name = None
    chat_display_name = None

    try:
        active_name = pm.resolve_name()
        path = pm.get_path(active_name)
        current_settings = get_settings(path)
        if current_settings:
            chat_display_name = get_chat_name(current_settings)
    except (ValueError, FileNotFoundError):
        console.print(
            "\n[yellow]Ningún perfil activo. Mostrando valores por defecto.[/yellow]"
        )

    title = "Configuración Global"
    if active_name:
        title += f" (Perfil Activo: [green]{active_name}[/green])"

    ui.render_options_table(
        title, schema, current_settings, chat_info=chat_display_name
    )
    ui.print_options_help_footer()


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


if __name__ == "__main__":
    set_config("chat_id", "-1001698464760")
