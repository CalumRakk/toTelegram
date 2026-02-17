from typing import TYPE_CHECKING, Optional

import typer
from click.core import Command
from pydantic import ValidationError
from rich.markup import escape
from rich.table import Table

from totelegram.commands.profile_ui import ProfileUI
from totelegram.commands.profile_utils import (
    _capture_chat_id_wizard,
    get_friendly_chat_name,
    handle_list_operation,
)
from totelegram.console import UI, console
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import CHAT_ID_NOT_SET, AccessLevel
from totelegram.services.validator import ValidationService
from totelegram.store.database import DatabaseSession
from totelegram.store.models import TelegramChat
from totelegram.telegram import TelegramSession
from totelegram.utils import normalize_chat_id

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import User

app = typer.Typer(help="Configuración del perfil actual.")
ui = ProfileUI(console)


def resolve_and_store_chat_logic(
    pm: ProfileManager,
    chat_alias: str,
    profile_name: str,
    client: Optional["Client"] = None,
):
    """
    Valida que un chat exista y lo guarda en la base de datos. Y incluye un fallback de permisos por consola.
    """
    normalized_key = normalize_chat_id(chat_alias)
    settings = pm.get_settings(profile_name)
    validator = ValidationService()

    def _execute(c: "Client"):
        chat_obj = validator.validate_chat_id(c, normalized_key)
        if not chat_obj:
            return False

        db_chat, created = TelegramChat.get_or_create_from_tg(chat_obj)
        if created:
            UI.success(f"Nuevo chat guardado: {db_chat.title} ({normalized_key})")
        else:
            db_chat.update_from_tg(chat_obj)
            UI.success(f"Chat actualizado: {db_chat.title} ({normalized_key})")

        pm.update_config("CHAT_ID", str(normalized_key), profile_name=profile_name)
        return True

    with DatabaseSession(settings.database_path):
        if client:
            return _execute(client)
        else:
            with TelegramSession(settings) as new_client:
                return _execute(new_client)

    return False

def mark_sensitive(value: int | str)-> str:
    if not isinstance(value, (str, int)):
        raise ValueError("mark_sensitive solo admite valores de tipo str o int para enmascarar.")

    value= str(value)
    if len(value) <= 6:
        display_val = "•" * len(value)
    else:
        display_val = value[:3] + "•" * (len(value) - 4) + value[-3:]

    return display_val


def display_config_table(pm: ProfileManager):
    table = Table(title="Configuración del Perfil", show_header=True, header_style="bold blue")
    table.add_column("Opción (Key)")
    table.add_column("Tipo")
    table.add_column("Valor Actual")
    table.add_column("Descripción")

    settings = pm.get_visible_settings()

    for field_name, value, access in settings:

        is_value_default= value != access.default_value
        value_style = "bold green"  if is_value_default else "dim white"

        if access.is_sensitive:
            display_val= mark_sensitive(value)
        else:
            display_val= str(value)

        if pm.is_debug and access.level == AccessLevel.DEBUG_READONLY:
            table.add_row(
                    f"[grey0]{field_name.lower()}[/]",
                    f"[grey0]{escape(access.type_annotation)}[/]",
                    f"[grey0]{display_val}[/]",
                    f"[grey0]{access.description}[/]",
                )
        elif pm.is_debug:
            table.add_row(
                    field_name.lower(),
                    escape(access.type_annotation),
                    f"[{value_style}]{display_val}[/]",
                    access.description,
                )
        elif access.level == AccessLevel.EDITABLE:
            table.add_row(
                    field_name.lower(),
                    escape(access.type_annotation),
                    f"[{value_style}]{display_val}[/]",
                    access.description,
                )

    console.print(table)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Muestra la configuración actual si no se pasa un subcomando."""
    if ctx.invoked_subcommand is not None:
        return

    try:
        pm: ProfileManager = ctx.obj
        active_name = pm.resolve_name()
        settings = pm.get_settings(active_name)
        chat_display_name = get_friendly_chat_name(settings)
    except (ValueError, FileNotFoundError):
        active_name = None
        settings = None
        chat_display_name = None
        UI.warn("Ningún perfil activo. Mostrando valores por defecto.")

    title = "Configuración"
    if active_name:
        title += f" (Perfil: [green]{active_name}[/green])"

    ui.announce_profile_used(active_name) if active_name else None
    display_config_table(pm)
    ui.print_options_help_footer()


@app.command("set", context_settings={"allow_interspersed_args": False})
def set_config(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Clave a modificar"),
    value: str = typer.Argument(..., help="Nuevo valor"),
):
    """Edita una configuración del perfil."""
    pm: ProfileManager = ctx.obj
    profile_name = pm.resolve_name()
    ui.announce_profile_used(profile_name)
    try:
        if key.upper() == "CHAT_ID":
            resolve_and_store_chat_logic(pm, value, profile_name)

        pm.update_config(key, value, profile_name=profile_name)
        UI.success(f"{key.upper()} -> '{value}'.")

    except (ValidationError, ValueError) as e:
        UI.error(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("unset")
def unset_config(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Clave a restaurar a su valor por defecto"),
):
    """Quita una configuración personalizada para usar el valor por defecto."""
    pm: ProfileManager = ctx.obj
    profile_name = pm.resolve_name()
    ui.announce_profile_used(profile_name)

    try:
        key = key.upper()
        if key == "CHAT_ID":
            UI.warn(
                "El CHAT_ID no tiene un valor por defecto seguro. Usa 'set' o el asistente (wizard)."
            )
            raise typer.Exit(code=1)

        settings = pm.get_settings(profile_name)

        was_reset, default_value = pm.unset_config(key, profile_name=profile_name)

        # Decidimos qué valor mostrar
        # Si NO hubo reset, sacamos el valor directamente del objeto settings (que ya es el default)
        # Si hubo reset, usamos el default_value que nos devolvió el manager
        actual_val = (
            default_value if was_reset else getattr(settings, key.lower(), None)
        )

        display_val = f"[green]{actual_val}[/green]"

        if was_reset:
            UI.success(f"Configuración restaurada: [bold]{key}[/]")
            console.print(f"   Ahora usa el valor por defecto: {display_val}")
        else:
            UI.info(
                f"La configuración [bold]{key}[/] ya estaba usando su valor por defecto."
            )
            console.print(f"   Valor actual: {display_val}")

    except ValueError as e:
        UI.error(str(e))
        raise typer.Exit(code=1)


@app.command("wizard")
def config_wizard(ctx: typer.Context):
    """Asistente interactivo para encontrar y configurar el chat de destino."""
    pm: ProfileManager = ctx.obj
    profile_name = pm.resolve_name()
    ui.announce_profile_used(profile_name)

    settings = pm.get_settings(profile_name)
    validator = ValidationService()

    try:
        UI.info(f"Iniciando cliente de telegram con sesión de {profile_name}...")
        with TelegramSession(settings) as client:
            UI.success("Cliente de telegram iniciado.")

            resolved_chat = _capture_chat_id_wizard(validator, client)

            if resolved_chat == "me":
                pm.update_config("CHAT_ID", "me", profile_name=profile_name)
                UI.success("Destino configurado: Mensajes Guardados")
            elif resolved_chat != CHAT_ID_NOT_SET:
                resolve_and_store_chat_logic(
                    pm, resolved_chat, profile_name, client=client
                )
            else:
                UI.info("Operación cancelada. No se han realizado cambios.")

    except Exception as e:
        UI.error(f"Error en el asistente: {e}")
        raise typer.Exit(1)


@app.command("add")
def add_to_list(ctx: typer.Context, key: str, values: list[str], force: bool = False):
    """Agrega valores a una lista (ej. EXCLUDE_FILES)."""
    pm: ProfileManager = ctx.obj
    profile_name = pm.resolve_name()
    handle_list_operation(pm, "add", key, values)


@app.command("remove")
def remove_from_list(
    ctx: typer.Context, key: str, values: list[str], force: bool = False
):
    """Elimina valores de una lista."""
    pm: ProfileManager = ctx.obj
    profile_name = pm.resolve_name()
    handle_list_operation(pm, "remove", key, values)


if __name__ == "__main__":
    cmd = Command("test")
    ctx = typer.Context(cmd)
    main(ctx)
