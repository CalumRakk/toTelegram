from typing import TYPE_CHECKING, Any, List, Literal, Optional, cast

import typer
from rich.markup import escape
from rich.table import Table

from totelegram.commands.profile_ui import ProfileUI
from totelegram.commands.profile_utils import (
    get_friendly_chat_name,
)
from totelegram.console import UI, console
from totelegram.core.registry import SettingsManager
from totelegram.core.schemas import CLIState
from totelegram.core.setting import AccessLevel, Settings

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import User

app = typer.Typer(help="Configuración del perfil actual.")
ui = ProfileUI(console)


def resolve_and_store_chat_logic(
    state: CLIState,
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


def mark_sensitive(value: int | str) -> str:
    if not isinstance(value, (str, int)):
        raise ValueError(
            "mark_sensitive solo admite valores de tipo str o int para enmascarar."
        )

    value = str(value)
    if len(value) <= 6:
        display_val = "•" * len(value)
    else:
        display_val = value[:3] + "•" * (len(value) - 4) + value[-3:]

    return display_val


def display_config_table(maneger: SettingsManager, is_debug: bool, settings: Settings):
    table = Table(
        title="Configuración del Perfil", show_header=True, header_style="bold blue"
    )
    table.add_column("Opción (Key)")
    table.add_column("Tipo")
    table.add_column("Valor Actual")
    table.add_column("Descripción")

    default_settings = maneger.get_default_settings()
    for field_name, default_value in default_settings.model_dump().items():
        info = Settings.get_info(field_name)
        if info is None:
            continue

        value = getattr(settings, field_name)
        is_value_default = value != default_value
        value_style = "bold green" if is_value_default else "dim white"

        # Si es CHAT_ID, lo hace amigable.
        if field_name.lower() == "chat_id":
            value = get_friendly_chat_name(value, str(maneger.database_path))

        # Oculta value sencille
        if info.is_sensitive:
            display_val = mark_sensitive(value)
        else:
            display_val = str(value)

        # Agrega a la tabla los campo segun el nivel de acceso.
        if is_debug and info.level == AccessLevel.DEBUG_READONLY:
            table.add_row(
                f"[grey0]{field_name.lower()}[/]",
                f"[grey0]{escape(info.type_annotation)}[/]",
                f"[grey0]{display_val}[/]",
                f"[grey0]{info.description}[/]",
            )
        elif is_debug:
            table.add_row(
                field_name.lower(),
                escape(info.type_annotation),
                f"[{value_style}]{display_val}[/]",
                info.description,
            )
        elif info.level == AccessLevel.EDITABLE:
            table.add_row(
                field_name.lower(),
                escape(info.type_annotation),
                f"[{value_style}]{display_val}[/]",
                info.description,
            )

    console.print(table)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Muestra la configuración actual si no se pasa un subcomando."""
    if ctx.invoked_subcommand is not None:
        return

    state: CLIState = ctx.obj
    manager = state.manager
    settings_name = manager.resolve_settings_name(state.settings_name, error=False)

    title = "Configuración"
    if settings_name:
        title += f" (Perfil: [green]{settings_name}[/green])"
        settings = manager.get_settings(settings_name)
    else:
        title += " (Sin Perfil Activo)"
        settings = manager.get_default_settings()

    ui.announce_profile_used(settings_name) if settings_name else None
    display_config_table(manager, state.is_debug, settings)
    ui.print_options_help_footer()


def parse_key_value_pairs(args: List[str]) -> dict[str, str]:
    """Convierte una lista de pares ([k1, v1, k2, v2]) a un diccionario: {k1: v1, k2: v2}."""
    if not args or len(args) % 2 != 0:
        UI.error("Debes proporcionar pares de CLAVE y VALOR. Ej: 'set chat_id 12345'")
        raise typer.Exit(1)

    # [k1, v1, k2, v2] -> {k1: v1, k2: v2}
    raw_data = {args[i].lower(): args[i + 1] for i in range(0, len(args), 2)}
    return raw_data


def transform_values(raw_data: dict[str, str]) -> dict[str, Any]:
    """Transforma los valores a los tipos correctos."""
    updates_to_apply = {}
    errors = []

    UI.info("Procesando cambios...")
    for key, raw_value in raw_data.items():
        try:
            clean_value = Settings.validate_single_setting(key, raw_value)
            updates_to_apply[key] = clean_value
        except ValueError as e:
            errors.append(f"[bold red]{key.upper()}[/]: {str(e)}")

    if errors:
        UI.error("Se encontraron errores de validacion. No se aplico ningun cambio:")
        for err in errors:
            console.print(f"  - {err}")
        raise typer.Exit(1)

    return updates_to_apply


def apply_changes(
    action: Literal["set", "add"],
    is_debug: bool,
    settings_name: str,
    manager: SettingsManager,
    updates_to_apply: dict[str, Any],
):
    """Aplica los cambios a la configuración.

    Solo aplica los cambios a los campos a los que se le tiene permiso de edicion.
    """
    results = []
    for field_name, field_value in updates_to_apply.items():
        try:
            Settings.validate_key_access(is_debug, field_name)
            if action == "set":
                result = manager.set_setting(settings_name, field_name, field_value)
            elif action == "add":
                result = manager.add_setting(settings_name, field_name, field_value)
            else:
                raise ValueError(f"Invalid action: {action}")

            results.append((field_name, result))
        except Exception as e:
            UI.error(f"Error persistiendo {field_name}: {e}")

    # if action == "set":
    #     UI.success("Cambios guardados.")
    # else:
    #     UI.success("Cambios añadidos.")

    for field_name, result in results:
        changed = result[0]
        value = result[1]
        if changed:
            UI.info(f"{field_name.upper()} -> '{value}'")
        else:
            UI.info(f"{field_name.upper()} -> No se modifico.")


@app.command(name="set")
def set_configs(
    ctx: typer.Context,
    args: List[str] = typer.Argument(
        None, help="Pares de CLAVE VALOR (ej: chat_id 12345 upload_limit_rate_kbps 500)"
    ),
):
    """
    Modifica una o varias configuraciones al mismo tiempo.
    Uso: totelegram config set chat_id 999999 upload_limit_rate_kbps 1000
    """
    state: CLIState = ctx.obj
    manager = state.manager
    is_debug = state.is_debug
    settings_name = cast(str, manager.resolve_settings_name(state.settings_name))

    ui.announce_profile_used(settings_name)

    raw_data = parse_key_value_pairs(args)
    updates_to_apply = transform_values(raw_data)

    apply_changes("set", is_debug, settings_name, manager, updates_to_apply)


@app.command("unset")
def unset_config(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Clave a restaurar a su valor por defecto"),
):
    """Quita una configuración personalizada para usar el valor por defecto."""
    # TODO: agregar soporte a multiples configuraciones. ¿deberia?
    state: CLIState = ctx.obj
    manager = state.manager
    is_debug = state.is_debug
    settings_name = cast(str, manager.resolve_settings_name(state.settings_name))

    ui.announce_profile_used(settings_name)

    settings = manager.get_settings(settings_name)

    info = Settings.validate_key_access(is_debug, key)

    current_value = getattr(settings, key.lower(), None)
    if current_value != info.default_value:
        manager.unset_setting(settings_name, key)
        manager.set_setting(settings_name, key, info.default_value)
        UI.success(
            f"La configuración [bold]{key}[/] ha sido restaurada a su valor por defecto."
        )
    else:
        UI.info(f"La configuración [bold]{key}[/] esta usando su valor por defecto.")


@app.command("add")
def add_to_list(ctx: typer.Context, key: str, values: List[str]):
    """Agrega valores a una lista (ej. EXCLUDE_FILES)."""
    # TODO: agregar soporte a multiples configuraciones ¿deberia?

    state: CLIState = ctx.obj
    manager = state.manager
    is_debug = state.is_debug
    settings_name = cast(str, manager.resolve_settings_name(state.settings_name))

    ui.announce_profile_used(settings_name)

    raw_data = parse_key_value_pairs([key, values])
    updates_to_apply = transform_values(raw_data)
    apply_changes("add", is_debug, settings_name, manager, updates_to_apply)


# @app.command("remove")
# def remove_from_list(
#     ctx: typer.Context, key: str, values: List[str], force: bool = False
# ):
#     """Elimina valores de una lista."""
#     env: Env = ctx.obj
#     profile_name = pm.resolve_name()
#     pm.update_config_list("remove", key, values, profile_name)
