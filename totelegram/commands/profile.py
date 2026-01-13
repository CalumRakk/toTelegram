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

    validator = ValidationService(console)
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


@app.command("use")
def use_profile(profile_name: str):
    """Cambia el perfil activo."""
    try:
        pm.activate(profile_name)
        console.print(
            f"[bold green]✔ Ahora usando el perfil: {profile_name}[/bold green]"
        )
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        list_profiles()


@app.command("set")
def set_config(
    key: str = typer.Argument(..., help="Clave a modificar"),
    value: str = typer.Argument(..., help="Nuevo valor"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p"),
):
    """Edita una configuración."""
    profile_name = profile or pm.active_name
    if not profile_name:
        console.print("[bold red]No hay perfil activo ni seleccionado.[/bold red]")
        return

    try:
        pm.update_config(key, value, profile_name=profile_name)

        console.print(
            f"[bold green]✔[/bold green] {key.upper()} actualizado en '[cyan]{profile_name}[/cyan]'."
        )

    except (ValidationError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("add")
def add_to_list(
    key: str, values: List[str], profile: Optional[str] = None, force: bool = False
):
    _handle_list_operation("add", key, values, profile, force)


@app.command("remove")
def remove_from_list(
    key: str, values: List[str], profile: Optional[str] = None, force: bool = False
):
    _handle_list_operation("remove", key, values, profile, force)


@app.command("list")
def list_profiles():
    """Enumera todos los perfiles registrados."""
    registry = pm.get_registry()
    if not registry.profiles:
        console.print("[yellow]No hay perfiles registrados.[/yellow]")
        return
    ui.render_profiles_table(registry.active, registry.profiles)


@app.command("options")
def list_options():
    """Lista las opciones de configuración y sus valores actuales."""
    schema = Settings.get_schema_info()
    current_settings = None
    active_name = None

    try:
        path = pm.get_path()
        current_settings = get_settings(path)
        active_name = pm.active_name
    except (ValueError, FileNotFoundError):
        console.print(
            "\n[yellow]Ningún perfil activo. Mostrando valores por defecto.[/yellow]"
        )

    title = "Configuración Global"
    if active_name:
        title += f" (Perfil Activo: [green]{active_name}[/green])"

    ui.render_options_table(title, schema, current_settings)
    ui.print_options_help_footer()


if __name__ == "__main__":
    create_profile("leo", 123456, "dfggdfdfhghfg", "your_chat_id")
