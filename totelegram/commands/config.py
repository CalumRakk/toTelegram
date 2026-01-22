from typing import TYPE_CHECKING

import typer
from click.core import Command
from pydantic import ValidationError

from totelegram.commands.profile_ui import ProfileUI
from totelegram.commands.profile_utils import (
    get_friendly_chat_name,
    handle_list_operation,
)
from totelegram.console import console
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import Settings, get_settings, normalize_chat_id
from totelegram.store.database import DatabaseSession
from totelegram.store.models import TelegramChat
from totelegram.telegram import TelegramSession

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import User

app = typer.Typer(help="Configuración del perfil actual.")
pm = ProfileManager()
ui = ProfileUI(console)


def resolve_and_store_chat_logic(chat_alias: str, profile_name: str):
    """
    Valida que un chat exista y lo guarda en la base de datos. Y incluye un fallback de permisos por consola.
    """
    from totelegram.services.validator import ValidationService

    normalized_key = normalize_chat_id(chat_alias)
    settings = pm.get_settings(profile_name)
    validator = ValidationService()

    with DatabaseSession(settings), TelegramSession(settings) as client:
        chat_obj = validator.validate_chat_id(client, normalized_key)
        if not chat_obj:
            return

        db_chat, created = TelegramChat.get_or_create_from_tg(chat_obj)
        if created:
            console.print(
                f"[green]✔ Nuevo chat guardado: [bold]{db_chat.title}[/bold] [dim]({normalized_key})[/dim][/green]"
            )
        else:
            db_chat.update_from_tg(chat_obj)
            console.print(
                f"[green]✔ Chat actualizado: [bold]{db_chat.title}[/bold] [dim]({normalized_key})[/dim][/green]"
            )

        pm.update_config("CHAT_ID", str(normalized_key), profile_name=profile_name)

        console.print(
            f"[green]✔ Configuración actualizada: [bold]{db_chat.title}[/bold] [dim]({normalized_key})[/dim][/green]"
        )
        return True

    return False


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Muestra la configuración actual si no se pasa un subcomando."""
    if ctx.invoked_subcommand is not None:
        return

    try:
        active_name = pm.resolve_name()
        path = pm.get_path(active_name)
        settings = get_settings(path)
        chat_display_name = get_friendly_chat_name(settings)
    except (ValueError, FileNotFoundError):
        active_name = None
        settings = None
        chat_display_name = None
        console.print(
            "\n[yellow]Ningún perfil activo. Mostrando valores por defecto.[/yellow]"
        )

    title = "Configuración"
    if active_name:
        title += f" (Perfil: [green]{active_name}[/green])"

    ui.announce_profile_used(active_name) if active_name else None
    ui.render_options_table(
        title, Settings.get_schema_info(), settings, chat_info=chat_display_name
    )
    ui.print_options_help_footer()


@app.command("set", context_settings={"allow_interspersed_args": False})
def set_config(
    key: str = typer.Argument(..., help="Clave a modificar"),
    value: str = typer.Argument(..., help="Nuevo valor"),
):
    """Edita una configuración del perfil."""
    profile_name = pm.resolve_name()
    ui.announce_profile_used(profile_name)
    try:
        if key.upper() == "CHAT_ID":
            resolve_and_store_chat_logic(value, profile_name)

        pm.update_config(key, value, profile_name=profile_name)
        console.print(
            f"[bold green]✔[/bold green] [bold]{key.upper()}[/bold] -> '{value}'."
        )

    except (ValidationError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("add")
def add_to_list(key: str, values: list[str], force: bool = False):
    """Agrega valores a una lista (ej. EXCLUDE_FILES)."""
    profile_name = pm.resolve_name()
    handle_list_operation("add", key, values, profile_name, force)


@app.command("remove")
def remove_from_list(key: str, values: list[str], force: bool = False):
    """Elimina valores de una lista."""
    profile_name = pm.resolve_name()
    handle_list_operation("remove", key, values, profile_name, force)


if __name__ == "__main__":
    cmd = Command("test")
    ctx = typer.Context(cmd)
    main(ctx)
