from typing import Optional

import typer
from pydantic import ValidationError

from totelegram.commands.profile_ui import ProfileUI
from totelegram.commands.profile_utils import _handle_list_operation
from totelegram.console import console
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import Settings, get_settings
from totelegram.store.database import DatabaseSession
from totelegram.store.models import TelegramChat

app = typer.Typer(help="Configuración del perfil actual.")
pm = ProfileManager()
ui = ProfileUI(console)


def _get_chat_display_name(current_settings) -> Optional[str]:
    """Resuelve el nombre del chat físico desde la DB local."""
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
    return None


def _try_resolve_and_store_chat(profile_name: str, chat_id: str):
    """Intenta conectar a Telegram para validar el chat y guardarlo en la DB."""
    path = pm.get_path(profile_name)
    settings = get_settings(path)
    session_file = pm.PROFILES_DIR / f"{profile_name}.session"

    if not session_file.exists():
        console.print(
            "[dim yellow]Nota: No hay archivo de sesión activo. Se resolverá en la próxima subida.[/dim yellow]"
        )
        return

    console.print(f"[dim]Resolviendo identidad del chat '{chat_id}'...[/dim]")
    from totelegram.services.validator import ValidationService
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


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Muestra la configuración actual si no se pasa un subcomando."""
    if ctx.invoked_subcommand is not None:
        return

    try:
        active_name = pm.resolve_name()
        path = pm.get_path(active_name)
        current_settings = get_settings(path)
        chat_display_name = _get_chat_display_name(current_settings)
    except (ValueError, FileNotFoundError):
        active_name = None
        current_settings = None
        chat_display_name = None
        console.print(
            "\n[yellow]Ningún perfil activo. Mostrando valores por defecto.[/yellow]"
        )

    title = "Configuración"
    if active_name:
        title += f" (Perfil: [green]{active_name}[/green])"

    ui.announce_profile_used(active_name) if active_name else None
    ui.render_options_table(
        title, Settings.get_schema_info(), current_settings, chat_info=chat_display_name
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
        validated_val = pm.update_config(key, value, profile_name=profile_name)
        console.print(
            f"[bold green]✔[/bold green] [bold]{key.upper()}[/bold] -> '{value}'."
        )
        if key.upper() == "CHAT_ID":
            _try_resolve_and_store_chat(profile_name, validated_val)
    except (ValidationError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("add")
def add_to_list(key: str, values: list[str], force: bool = False):
    """Agrega valores a una lista (ej. EXCLUDE_FILES)."""
    profile_name = pm.resolve_name()
    _handle_list_operation("add", key, values, profile_name, force)


@app.command("remove")
def remove_from_list(key: str, values: list[str], force: bool = False):
    """Elimina valores de una lista."""
    profile_name = pm.resolve_name()
    _handle_list_operation("remove", key, values, profile_name, force)
