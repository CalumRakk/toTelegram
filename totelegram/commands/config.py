import json
import time
from functools import wraps
from typing import TYPE_CHECKING, List, Literal, Tuple, cast

import typer

from totelegram.commands.views import DisplayConfig, DisplayGeneric, DisplayProfile
from totelegram.console import UI
from totelegram.core.consts import VALUE_NOT_SET, Commands
from totelegram.core.schemas import CLIState
from totelegram.core.setting import normalize_chat_id
from totelegram.services.chat_access import ChatAccessService
from totelegram.services.chat_search import ChatSearchService
from totelegram.services.config_service import ConfigService
from totelegram.telegram import TelegramSession
from totelegram.utils import (
    is_direct_identifier,
    is_potential_username,
    is_suspected_glob_expansion,
    validate_item,
)

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import User

app = typer.Typer(help="Configuración del perfil actual.")


def handle_config_errors(func):
    @wraps(func)
    def wrapper(ctx: typer.Context, *args, **kwargs):
        try:
            return func(ctx, *args, **kwargs)
        except ValueError as e:
            UI.error(str(e))
            raise typer.Exit(1)

    return wrapper


def _get_config_tools(ctx: typer.Context) -> Tuple[str, ConfigService]:
    """Extrae las herramientas necesarias para cualquier comando de configuracion que requieran un perfil."""
    state: CLIState = ctx.obj
    settings_name = cast(str, state.manager.resolve_profile_name(state.profile_name))
    DisplayProfile.announce_profile_used(settings_name)
    service = ConfigService(state.manager, state.is_debug)
    return settings_name, service


@handle_config_errors
def _command_modify_list(
    settings_name: str,
    service: ConfigService,
    key: str,
    values: List[str],
    action: Literal["add", "remove"],
):
    updates = service.prepare_updates([key, values])  # type: ignore
    verb = "agregar" if action == "add" else "eliminar"

    if is_suspected_glob_expansion(values):
        if not DisplayConfig.confirm_expanded_pattern(verb, key, values):
            UI.info("Operacion cancelada.")
            raise typer.Exit()

    for k, val in updates.items():
        changed, final_val = service.apply_update(settings_name, k, val, action=action)
        if changed:
            UI.success(
                f"Configuracion [bold]{k}[/] actualizada: {json.dumps(final_val)}"
            )
        else:
            UI.info(f"Configuracion [bold]{k}[/] no sufrio cambios.")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Muestra la configuración actual si no se pasa un subcomando."""
    if ctx.invoked_subcommand is not None:
        return

    state: CLIState = ctx.obj
    manager = state.manager
    profile_name = manager.resolve_profile_name(state.profile_name, strict=False)
    if profile_name:
        settings = manager.get_settings(profile_name)
        DisplayConfig.show_config_table(profile_name, manager, state.is_debug, settings)

        commands = [
            f"{Commands.CONFIG_SET} <KEY> <VALUE>",
            f"{Commands.CONFIG_EDIT_LIST} <KEY> <VALUE1>",
        ]
        UI.tip(
            "Puedes modificar cualquier configuración usando los siguientes comandos:",
            commands=commands,
            spacing="top",
        )
    else:
        UI.warn("No hay un perfil activo para mostrar valores de configuracion.")


@app.command(name="set")
@handle_config_errors
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

    settings_name, service = _get_config_tools(ctx)
    updates = service.prepare_updates(args)
    for key, val in updates.items():
        changed, final_val = service.apply_update(settings_name, key, val, action="set")
        if changed:
            UI.success(
                f"Configuracion [bold]{key}[/] actualizada con [bold]{final_val}[/]."
            )
        else:
            UI.info(f"Configuracion [bold]{key}[/] ya tiene ese valor.")


@app.command("unset")
@handle_config_errors
def unset_config(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Clave a restaurar a su valor por defecto"),
):
    """Quita una configuración personalizada para usar el valor por defecto."""

    settings_name, service = _get_config_tools(ctx)
    default_val = service.restore_default(settings_name, key)
    UI.success(f"Restaurado [bold]{key}[/] al valor por defecto: {default_val}")


@app.command("add")
def add_config(
    ctx: typer.Context,
    key: str,
    values: List[str] = typer.Argument(
        ..., min=1, callback=lambda x: [validate_item(i) for i in x]
    ),
):
    """Agrega valores a una lista (ej. exclude_files)."""
    settings_name, service = _get_config_tools(ctx)
    _command_modify_list(settings_name, service, key, values, "add")


@app.command("remove")
def remove_config(
    ctx: typer.Context,
    key: str,
    values: List[str] = typer.Argument(
        ..., min=1, callback=lambda x: [validate_item(i) for i in x]
    ),
):
    """Quita valores a una lista (ej. exclude_files)."""
    settings_name, service = _get_config_tools(ctx)
    _command_modify_list(settings_name, service, key, values, "remove")


@app.command("check")
@handle_config_errors
def check_config(ctx: typer.Context):
    """Verifica que el perfil actual esté listo para subir archivos."""
    state: CLIState = ctx.obj
    manager = state.manager

    profile_name = manager.resolve_profile_name(state.profile_name, strict=False)
    if not profile_name:
        UI.error("No hay un perfil activo o el perfil especificado no existe.")
        raise typer.Exit(1)

    with UI.loading(
        f"Comprobando integridad local del perfil: [bold]{profile_name}[/]"
    ):
        time.sleep(0.2)
        profile = manager.get_profile(profile_name)

        if not profile:
            UI.error(f"Perfil '{profile_name}' no encontrado.")
            raise typer.Exit(1)

        if not profile.is_trinity:
            UI.error(f"El perfil '{profile_name}' está incompleto.")
            if not profile.has_env:
                UI.info("Falta el archivo de configuración (.env).")
            if not profile.has_session:
                UI.info("Falta el archivo de sesión de Telegram (.session).")

            command = f"{Commands.PROFILE_CREATE} --force"
            UI.tip(
                f"Créalo de nuevo usando el siguiente comando:",
                commands=command,
                spacing="top",
            )
            raise typer.Exit(1)

    settings = manager.get_settings(profile_name)

    if settings.chat_id == VALUE_NOT_SET:
        UI.warn("El destino (chat_id) no está configurado.")
        UI.tip(
            "Configura el destino usando uno de estos comandos:",
            commands=[
                f"{Commands.CONFIG_SET} chat_id <ID>",
                f"{Commands.CONFIG_SEARCH} <NOMBRE>",
            ],
        )
        raise typer.Exit(code=1)

    with TelegramSession.from_profile(profile_name, manager) as client:
        with UI.loading("Verificando conexión y permisos en Telegram..."):
            validator = ChatAccessService(client)
            access_report = validator.verify_access(settings.chat_id)

        if not access_report.is_ready:
            UI.warn("No tienes permisos de escritura en el chat destino.")
            UI.info(f"Motivo: {access_report.reason}")
            if access_report.hint:
                UI.info(access_report.hint)
            raise typer.Exit(code=1)

        UI.success(f"Perfil '{profile_name}' verificado y listo para operar.")
        if access_report.chat:
            UI.info(
                f"Destino confirmado: [bold]{access_report.chat.title}[/] "
                f"[dim](ID: {access_report.chat.id})[/dim]"
            )


@app.command("search")
def search_config(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Nombre, @username o ID."),
    contains: bool = typer.Option(False, "--contains", "-c"),
    depth: int = typer.Option(100, "--depth", "-d"),
    apply: bool = typer.Option(False, "--apply", "-a"),
):
    """Busca y establece el chat de destino."""
    state: CLIState = ctx.obj
    manager = state.manager
    profile_name = cast(str, manager.resolve_profile_name(state.profile_name))

    DisplayProfile.announce_profile_used(profile_name)

    # Obtener el ID del chat destino
    with TelegramSession.from_profile(profile_name, manager) as client:
        chat_access = ChatAccessService(client)
        chat_searcher = ChatSearchService(client)

        me = cast("User", client.get_me())
        UI.info(f"Telegram Session: [bold]{me.username or me.first_name}[/]")

        target_chat_id = None

        if is_direct_identifier(query):
            target_chat_id = normalize_chat_id(query)
        else:
            is_exact = not contains
            search_desc = (
                f"llamado exactamente '[bold]{query}[/]'"
                if is_exact
                else f"que contenga '[bold]{query}[/]' (sin distinción de mayúsculas y minúsculas)"
            )
            UI.info(f"Buscando chat {search_desc}")
            with UI.loading("Explorando los chats recientes..."):
                result = chat_searcher.search_by_name(query, depth, is_exact)

            if result.is_resolved and result.winner:
                target_chat_id = result.winner.id

            elif result.is_ambiguous:
                conflicts = len(result.conflicts)
                UI.warn(f"Ambigüedad: Hay {conflicts} chats con ese nombre.")
                DisplayGeneric.show_chat_table(
                    result.conflicts, "Conflictos Encontrados"
                )
                UI.info("Usa el ID exacto: [bold]config set chat_id <ID>[/]")
                raise typer.Exit(1)

            elif result.needs_help:

                DisplayGeneric.show_chat_table(
                    result.suggestions, "Quizás quisiste decir:"
                )
                UI.info(
                    "Tip: Si no encuentras lo que buscas, intenta aumentar la profundidad con --depth."
                )
                raise typer.Exit(1)
            else:
                UI.error(
                    f"No se encontró ningún chat relacionado con '{result.query}' en los primeros {result.search_depth} chats recientes."
                )
                if is_potential_username(result.query):
                    UI.tip(
                        message="Puedes hacer una busqueda más profunda con [bold]--depth[/] o especificar el chat directamente con @username o t.me/username.",
                        commands=[
                            f"{Commands.CONFIG_SEARCH} {result.query} --depth 200",
                            f"{Commands.CONFIG_SEARCH} @{result.query}",
                        ],
                    )

                raise typer.Exit(1)

        # Verificación de permisos
        with UI.loading("Verificando permisos..."):
            report = chat_access.verify_access(target_chat_id)

        if not (report.is_ready and report.chat):
            UI.warn("No tienes permisos de escritura en el chat.")
            UI.warn("Corrige los permisos antes de intentar cualquier subida.")
            UI.warn("Configuración 'chat_id' no actualizada.")
            raise typer.Exit(1)

        # Si llegamos aquí, todo salió bien
        UI.success(
            f"¡Encontrado! [bold]{report.chat.title}[/] [dim](ID: {report.chat.id})[/]"
        )
        UI.success("Tienes permisos de escritura.")

        if apply:
            manager.set_setting(profile_name, "chat_id", report.chat.id)
            UI.success("Configuración 'chat_id' actualizada.")

        raise typer.Exit(0)
