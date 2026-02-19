from typing import TYPE_CHECKING, List, cast

import typer

from totelegram.commands.config_logic import (
    ConfigResolutionLogic,
    ConfigUpdateLogic,
    display_config_table,
)
from totelegram.commands.profile_ui import ProfileUI
from totelegram.console import UI, console
from totelegram.core.schemas import CLIState
from totelegram.core.setting import Settings
from totelegram.services.chat_resolver import ChatResolverService
from totelegram.services.validator import ValidationService
from totelegram.telegram import TelegramSession

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import User

app = typer.Typer(help="Configuración del perfil actual.")
ui = ProfileUI(console)


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

    NOTA:
    Evitar implementar lógica de red o resolución de Telegram (como 'config resolve')
    dentro de este comando por las siguientes razones:

    1. INDEPENDENCIA: 'config set' debe funcionar 100% offline. Su única responsabilidad
       es la persistencia en disco (.env). No debe depender de una sesión de Pyrogram.
    2. ATOMICIDAD: Este comando puede recibir múltiples pares clave-valor.
       Añadir banderas como '--verify' o '--resolve' crearía ambigüedad sobre qué
       campo se está verificando o resolviendo.
    3. PREDICTIBILIDAD: Para automatización, 'set' debe ser instantáneo.
       Cualquier validación de red debe delegarse al comando 'config check' o
       'config resolve'.
    """
    state: CLIState = ctx.obj
    manager = state.manager
    settings_name = cast(str, manager.resolve_settings_name(state.settings_name))

    ui.announce_profile_used(settings_name)

    logic = ConfigUpdateLogic(settings_name, manager, state.is_debug)
    updates = logic.parse_and_transform(args)
    logic.apply(settings_name, updates, action="set")


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


def validate_item(value: str) -> str:
    if "," in value or value.strip().startswith("["):
        UI.error("Formato no soportado. Usa: config <key> add 1 2 3")
        raise typer.Exit()
    return value


@app.command("add")
def add_to_list(
    ctx: typer.Context,
    key: str,
    values: List[str] = typer.Argument(
        ..., min=1, callback=lambda x: [validate_item(i) for i in x]
    ),
):
    """Agrega valores a una lista (ej. EXCLUDE_FILES)."""
    # TODO: agregar soporte a multiples configuraciones ¿deberia?

    state: CLIState = ctx.obj
    manager = state.manager
    is_debug = state.is_debug
    settings_name = cast(str, manager.resolve_settings_name(state.settings_name))

    ui.announce_profile_used(settings_name)

    logic = ConfigUpdateLogic(settings_name, manager, state.is_debug)

    updates_to_apply = logic.parse_and_transform([key, values])  # type: ignore
    logic.apply(settings_name, updates_to_apply, action="add")


# @app.command("wizard")
# def config_wizard(ctx: typer.Context):
#     """Asistente interactivo para encontrar y configurar el chat de destino."""
#     state: CLIState = ctx.obj
#     manager = state.manager
#     is_debug = state.is_debug
#     settings_name = cast(str, manager.resolve_settings_name(state.settings_name))
#     ui.announce_profile_used(settings_name)

#     settings = manager.get_settings(settings_name)
#     validator = ValidationService()

#     try:
#         UI.info(f"Iniciando cliente de telegram con sesión de {settings_name}...")

#         with TelegramSession(manager.worktable, settings) as client:
#             UI.success("Cliente de telegram iniciado.")

#             resolved_chat = _capture_chat_id_wizard(validator, client)

#             if resolved_chat == "me":
#                 pm.update_config("CHAT_ID", "me", profile_name=profile_name)
#                 UI.success("Destino configurado: Mensajes Guardados")
#             elif resolved_chat != CHAT_ID_NOT_SET:
#                 resolve_and_store_chat_logic(
#                     pm, resolved_chat, profile_name, client=client
#                 )
#             else:
#                 UI.info("Operación cancelada. No se han realizado cambios.")

#     except Exception as e:
#         UI.error(f"Error en el asistente: {e}")
#         raise typer.Exit(1)


@app.command("check")
def check(ctx: typer.Context):
    """Verifica que el perfil actual esté listo para subir archivos."""
    state: CLIState = ctx.obj
    manager = state.manager
    settings_name = cast(str, manager.resolve_settings_name(state.settings_name))

    settings = state.manager.get_settings(settings_name)
    UI.info(f"Comprobando integridad del perfil: [bold]{state.settings_name}[/]")

    try:
        with TelegramSession(state.manager.profiles_dir, settings=settings) as client:
            from pyrogram.types import Chat, User

            with UI.loading("Validando usuario..."):
                me = cast(User, client.get_me())

            UI.success(f"Conectado como: {me.first_name} (@{me.username})")

            if settings.chat_id == "NOT_SET":
                UI.warn("CHAT_ID no configurado.")
                raise typer.Exit(code=1)

            with UI.loading("Validando chat destino..."):
                chat = cast(Chat, client.get_chat(settings.chat_id))

            UI.success(f"Destino válido: {chat.title} ({chat.type})")

            with UI.loading("Verificando permisos..."):
                validator = ValidationService()
                has_permissions = validator._verify_permissions(client, chat)

            if not has_permissions:
                UI.warn("No tienes permisos de escritura en el chat.")
                raise typer.Exit(code=1)

            UI.success("Perfil listo para subir archivos.")

    except Exception as e:
        UI.error(f"Error de configuracion: {str(e)}")
        raise typer.Exit(code=1)


@app.command("resolve")
def resolve(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Nombre, @username o ID."),
    contains: bool = typer.Option(False, "--contains", "-c"),
    depth: int = typer.Option(100, "--depth", "-d"),
    apply: bool = typer.Option(False, "--apply", "-a"),
):
    state: CLIState = ctx.obj
    manager = state.manager
    settings_name = cast(str, manager.resolve_settings_name(state.settings_name))
    settings = manager.get_settings(settings_name)

    logic = ConfigResolutionLogic(manager)
    with console.status("Iniciando cliente de telegram...") as status:
        with TelegramSession(manager.profiles_dir, settings=settings) as client:
            status.stop()

            resolver = ChatResolverService(client)

            # TODO: mejorar el mensaje. Si se especifica un ID aparece como "chat llamado..."
            query_clean = query.replace("ID:", "").replace("id:", "").strip()
            search_desc = (
                f"que contenga '[bold]{query_clean}[/]' (sin distinción de mayúsculas y minúsculas)"
                if contains
                else f"llamado exactamente '[bold]{query_clean}[/]'"
            )

            UI.info(f"Buscando chat {search_desc}")
            with UI.loading("Resolviendo..."):
                result = resolver.resolve(
                    query_clean, is_exact=not contains, depth=depth
                )

            logic.handle_search_result(client, result, settings_name, apply)


# @app.command("remove")
# def remove_from_list(
#     ctx: typer.Context, key: str, values: List[str], force: bool = False
# ):
#     """Elimina valores de una lista."""
#     env: Env = ctx.obj
#     profile_name = pm.resolve_name()
#     pm.update_config_list("remove", key, values, profile_name)
