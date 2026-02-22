from typing import TYPE_CHECKING, List, cast

import typer

from totelegram.commands.views import DisplayConfig, DisplayGeneric, DisplayProfile
from totelegram.console import UI, console
from totelegram.core.schemas import CLIState
from totelegram.core.setting import Settings, normalize_chat_id
from totelegram.services.chat_access import ChatAccessService
from totelegram.services.chat_search import ChatSearchService
from totelegram.services.config_service import ConfigService
from totelegram.telegram import TelegramSession
from totelegram.utils import is_direct_identifier

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import User

app = typer.Typer(help="Configuración del perfil actual.")

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Muestra la configuración actual si no se pasa un subcomando."""
    if ctx.invoked_subcommand is not None:
        return

    state: CLIState = ctx.obj
    manager = state.manager
    profile_name = manager.resolve_profile_name(state.profile_name, strict=False)

    title = "Configuración"
    if profile_name:
        title += f" (Perfil: [green]{profile_name}[/green])"
        settings = manager.get_settings(profile_name)
    else:
        title += " (Sin Perfil Activo)"
        settings = Settings.get_default_settings()

    DisplayProfile.announce_profile_used(profile_name) if profile_name else None
    DisplayConfig.show_config_table(manager, state.is_debug, settings)
    console.print(
            "\nUsa [yellow]totelegram profile set <KEY> <VALUE>[/yellow] para modificar."
        )
    console.print(
            "Usa [yellow]totelegram profile add/remove[/yellow] para listas (ej: EXCLUDE_FILES)."
        )


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
    settings_name = cast(str, manager.resolve_profile_name(state.profile_name))

    DisplayProfile.announce_profile_used(settings_name)

    try:
        service = ConfigService(state.manager, state.is_debug)
        updates = service.prepare_updates(args)
        for key, val in updates.items():
            changed, final_val = service.apply_update(
                settings_name, key, val, action="set"
            )
            if changed:
                UI.success(f"Configuracion [bold]{key}[/] actualizada.")
            else:
                UI.info(f"Configuracion [bold]{key}[/] ya tiene ese valor.")

        settings = state.manager.get_settings(settings_name)
        DisplayConfig.show_config_table(state.manager, state.is_debug, settings)

    except ValueError as e:
        UI.error(f"DEBUG ERROR: {str(e)}")
        raise typer.Exit(1)


@app.command("unset")
def unset_config(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Clave a restaurar a su valor por defecto"),
):
    """Quita una configuración personalizada para usar el valor por defecto."""
    # TODO: agregar soporte a multiples configuraciones. ¿deberia?
    state: CLIState = ctx.obj
    manager = state.manager
    settings_name = cast(str, manager.resolve_profile_name(state.profile_name))

    DisplayProfile.announce_profile_used(settings_name)

    try:
        service = ConfigService(state.manager, state.is_debug)
        default_val = service.restore_default(settings_name, key)
        UI.success(f"Restaurado [bold]{key}[/] al valor por defecto: {default_val}")
    except ValueError as e:
        UI.error(str(e))
        raise typer.Exit(1)


def validate_item(value: str) -> str:
    if "," in value or value.strip().startswith("["):
        UI.error("Formato no soportado. Usa: config <key> add 1 2 3")
        raise typer.Exit()
    return value


@app.command("add")
def add_config(
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
    settings_name = cast(str, manager.resolve_profile_name(state.profile_name))

    DisplayProfile.announce_profile_used(settings_name)

    try:
        service = ConfigService(state.manager, state.is_debug)
        updates = service.prepare_updates([key, values])  # type: ignore
        for key, val in updates.items():
            changed, final_val = service.apply_update(
                settings_name, key, val, action="set"
            )
            if changed:
                UI.success(f"Configuracion [bold]{key}[/] actualizada.")
            else:
                UI.info(f"Configuracion [bold]{key}[/] ya tiene ese valor.")

        settings = state.manager.get_settings(settings_name)
        DisplayConfig.show_config_table(state.manager, state.is_debug, settings)

    except ValueError as e:
        UI.error(str(e))
        raise typer.Exit(1)


@app.command("check")
def check_config(ctx: typer.Context):
    """Verifica que el perfil actual esté listo para subir archivos."""
    # FIX: si `.session` no existe, el comando creara uno en la carpeta de perfiles. ocacionando confusion con la trinidad.
    state: CLIState = ctx.obj
    manager = state.manager
    settings_name = cast(str, manager.resolve_profile_name(state.profile_name))

    settings = state.manager.get_settings(settings_name)
    UI.info(f"Comprobando integridad del perfil: [bold]{state.profile_name}[/]")

    try:
        with TelegramSession(
            session_name=settings_name,
            api_hash=settings.api_hash,
            api_id=settings.api_id,
            profiles_dir=manager.profiles_dir,
        ) as client:
            from pyrogram.types import Chat

            if settings.chat_id == "NOT_SET":
                DisplayConfig.show_config_table(manager, state.is_debug, settings)
                UI.warn("CHAT_ID no configurado.")
                raise typer.Exit(code=1)

            with UI.loading("Verificando permisos..."):
                validator = ChatAccessService(client)
                access_report = validator.verify_access(settings.chat_id)

            if not access_report.is_ready:
                UI.warn("No tienes permisos de escritura en el chat.")
                raise typer.Exit(code=1)

            UI.success("Perfil listo para subir archivos.")

    except Exception as e:
        UI.error(f"Error de configuracion {str(e)}")
        raise typer.Exit(code=1)


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

        target_chat_id = None

        if is_direct_identifier(query):
            target_chat_id = normalize_chat_id(query)
        else:
            is_exact = not contains
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
                    f"No se encontró ningún chat relacionado con '{result.query}'."
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


# @app.command("remove")
# def remove_from_list(
#     ctx: typer.Context, key: str, values: List[str], force: bool = False
# ):
#     """Elimina valores de una lista."""
#     env: Env = ctx.obj
#     profile_name = pm.resolve_name()
#     pm.update_config_list("remove", key, values, profile_name)
