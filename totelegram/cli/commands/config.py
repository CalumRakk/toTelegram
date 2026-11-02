import json
import time
from functools import wraps
from typing import Literal

import typer

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI
from totelegram.cli.views.settings import DisplayConfig
from totelegram.database import DatabaseSession
from totelegram.identity import ConfigService
from totelegram.migration import DatabaseState, inspect_database
from totelegram.models import TelegramChat
from totelegram.schemas import VALUE_NOT_SET, Commands
from totelegram.telegram.access import ChatAccessService
from totelegram.telegram.utils import (
    normalize_chat_id,
)
from totelegram.utils import (
    is_suspected_glob_expansion,
    validate_item,
)

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


def _validate_live_chat_id(
    chat_id_value: str | int, profile_name: str, state: CLIState
) -> tuple[int | str, str]:
    """Valida los permisos del chat en Telegram y retorna (chat_id_normalizado, titulo_chat)."""
    normalized_id = normalize_chat_id(chat_id_value)

    if normalized_id == VALUE_NOT_SET:
        return VALUE_NOT_SET, ""

    with state.get_telegram_session(profile_name) as client:
        with UI.loading("Verificando acceso y permisos en Telegram..."):
            access_service = ChatAccessService(client)
            report = access_service.verify_access(normalized_id)

        if not report.is_ready or not report.chat:
            hint = f"\n[dim]{report.hint}[/dim]" if report.hint else ""
            raise ValueError(f"Chat inválido: {report.reason}{hint}")

        # Guardar / Actualizar en la base de datos local para que el nombre quede en caché
        try:
            with DatabaseSession(state.manager.database_path, auto_init_schema=True):
                tg_chat_obj = client.get_chat(report.chat.id)
                TelegramChat.get_or_create_from_chat(tg_chat_obj)  # type: ignore
        except Exception:  # noqa: S110
            pass  # Si la DB aún no está inicializada, no bloqueamos la configuración

        title = report.chat.title or "Mensajes Guardados"
        UI.success(
            f"Destino confirmado: [bold cyan]{title}[/] [dim](ID: {report.chat.id})[/]"
        )
        return report.chat.id, title


def _get_config_tools(ctx: typer.Context) -> tuple[str, ConfigService]:
    """Extrae las herramientas necesarias para cualquier comando de configuracion que requieran un perfil."""
    state: CLIState = ctx.obj
    state.announce_profile()
    service = ConfigService(state.manager, state.is_debug)
    return state.profile_name, service


@handle_config_errors
def _command_modify_list(
    settings_name: str,
    service: ConfigService,
    key: str,
    values: list[str],
    action: Literal["add", "remove"],
):
    verb = "agregar" if action == "add" else "eliminar"

    if is_suspected_glob_expansion(
        values
    ) and not DisplayConfig.confirm_expanded_pattern(verb, key, values):
        UI.info("Operación cancelada.")
        raise typer.Exit()

    changed, final_val = service.modify_list_setting(
        settings_name, key, values, action=action
    )
    if changed:
        UI.success(f"Configuración [bold]{key}[/] actualizada: {json.dumps(final_val)}")
    else:
        UI.info(f"Configuración [bold]{key}[/] no sufrió cambios.")


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
    args: list[str] = typer.Argument(
        ..., help="Pares de CLAVE VALOR (ej: chat_id 12345 upload_limit_rate_kbps 500)"
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Fuerza el guardado sin verificar la conectividad de los endpoints de red (modo offline).",
    ),
):
    """
    Modifica una o varias configuraciones. Valida automáticamente endpoints de red
    (chat_id) en tiempo real a menos que se use --force.
    """

    settings_name, service = _get_config_tools(ctx)
    updates = service.prepare_updates(args)

    for key, val in updates.items():
        changed, final_val = service.apply_set(settings_name, key, val)
        if changed:
            UI.success(
                f"Configuración [bold]{key}[/] actualizada con [bold]{final_val}[/]."
            )
        else:
            UI.info(f"Configuración [bold]{key}[/] ya tiene ese valor.")


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
    values: list[str] = typer.Argument(
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
    values: list[str] = typer.Argument(
        ..., min=1, callback=lambda x: [validate_item(i) for i in x]
    ),
):
    """Quita valores a una lista (ej. exclude_files)."""
    settings_name, service = _get_config_tools(ctx)
    _command_modify_list(settings_name, service, key, values, "remove")


@app.command("check")
@handle_config_errors
def check_config(ctx: typer.Context):
    """Verifica que el perfil actual esté listo para operar."""
    state: CLIState = ctx.obj
    manager = state.manager

    profile_name = state.get_profile_name(strict=False)
    if not profile_name:
        UI.error("No hay un perfil activo o el perfil especificado no existe.")
        raise typer.Exit(1)

    # 1. Verificación de integridad de archivos locales (.json y .session)
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
            if not profile.has_config:
                UI.info("Falta el archivo de configuración (.json).")
            if not profile.has_session:
                UI.info("Falta el archivo de sesión de Telegram (.session).")
            raise typer.Exit(1)

    # 2. Verificación de la Base de Datos SQLite local y Esquema
    with UI.loading("Verificando conexión y esquema de base de datos..."):
        try:
            with DatabaseSession(manager.database_path, auto_init_schema=False) as db:
                report = inspect_database(db)

                if report.state == DatabaseState.UP_TO_DATE:
                    UI.success(
                        f"Base de datos: [bold]SQLite[/] al día (v{report.current_version})."
                    )
                elif report.state == DatabaseState.OUTDATED:
                    UI.warn(
                        f"Base de datos en versión anterior detectada (v{report.current_version})."
                    )
                    UI.info(
                        f"Al ejecutar subidas, se archivará automáticamente como "
                        f"'{manager.database_path.stem}.legacy_v{report.current_version}.sqlite' "
                        f"y se iniciará una base de datos limpia v{report.target_version}."
                    )
                elif report.state == DatabaseState.FRESH:
                    UI.info(
                        f"Base de datos: [bold]SQLite[/] nueva (se inicializará en v{report.target_version})."
                    )
                elif report.state == DatabaseState.AHEAD:
                    UI.error(
                        f"Base de datos incompatible: versión v{report.current_version} en base de datos, "
                        f"pero el cliente solo soporta hasta v{report.target_version}."
                    )
                    UI.info(
                        "Acción requerida: Actualiza toTelegram (`pip install --upgrade totelegram`)."
                    )
                    raise typer.Exit(1)
                elif report.state == DatabaseState.CORRUPTED:
                    UI.error(
                        f"Base de datos corrupta o inconsistente: {report.details}"
                    )
                    raise typer.Exit(1)

        except typer.Exit:
            raise
        except Exception as e:
            UI.error(f"Error conectando a la base de datos: {e}")
            raise typer.Exit(1)

    # 3. Verificación de Conexión activa con Telegram
    with state.get_telegram_session(profile_name) as client:
        with UI.loading("Verificando conexión y credenciales con Telegram..."):
            try:
                me = client.get_me()
                if not me:
                    UI.error(
                        "No se pudo obtener la información de la cuenta en Telegram."
                    )
                    raise typer.Exit(1)
            except Exception as e:
                UI.error(f"Error de conexión con los servidores de Telegram: {e}")
                raise typer.Exit(1)

        tier_badge = (
            "[bold cyan]Premium ★[/]"
            if getattr(me, "is_premium", False)
            else "Estándar"
        )
        user_name = me.first_name or me.username or "Usuario"

        UI.success(
            f"Conexión con Telegram exitosa: [bold]{user_name}[/] ({tier_badge})"
        )
        UI.success(
            f"Perfil '[bold green]{profile_name}[/]' verificado y listo para operar."
        )
        UI.print(
            "[dim]Nota: Para subir o respaldar archivos recuerda indicar el destino con [bold]-t / --target[/].[/dim]",
            spacing="top",
        )
