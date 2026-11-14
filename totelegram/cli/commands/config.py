import json
import time
from functools import wraps
from typing import Literal

import typer

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI
from totelegram.cli.views.settings import DisplayConfig
from totelegram.database import DatabaseSession
from totelegram.i18n import _
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

app = typer.Typer(help=_("Configuración del perfil actual."))


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
        with UI.loading(_("Verificando acceso y permisos en Telegram...")):
            access_service = ChatAccessService(client)
            report = access_service.verify_access(normalized_id)

        if not report.is_ready or not report.chat:
            hint = f"\n[dim]{report.hint}[/dim]" if report.hint else ""
            raise ValueError(
                _("Chat inválido: {reason}{hint}").format(
                    reason=report.reason, hint=hint
                )
            )

        # Guardar / Actualizar en la base de datos local para que el nombre quede en caché
        try:
            with DatabaseSession(state.manager.database_path, auto_init_schema=True):
                tg_chat_obj = client.get_chat(report.chat.id)
                TelegramChat.get_or_create_from_chat(tg_chat_obj)  # type: ignore
        except Exception:  # noqa: S110
            pass  # Si la DB aún no está inicializada, no bloqueamos la configuración

        title = report.chat.title or _("Mensajes Guardados")
        UI.success(
            _(
                "Destino confirmado: [bold cyan]{title}[/] [dim](ID: {chat_id})[/]"
            ).format(title=title, chat_id=report.chat.id)
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
        UI.info(_("Operación cancelada."))
        raise typer.Exit()

    changed, final_val = service.modify_list_setting(
        settings_name, key, values, action=action
    )
    if changed:
        UI.success(
            _("Configuración [bold]{key}[/] actualizada: {value}").format(
                key=key, value=json.dumps(final_val)
            )
        )
    else:
        UI.info(_("Configuración [bold]{key}[/] no sufrió cambios.").format(key=key))


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
            _(
                "Puedes modificar cualquier configuración usando los siguientes comandos:"
            ),
            commands=commands,
            spacing="top",
        )
    else:
        UI.warn(_("No hay un perfil activo para mostrar valores de configuracion."))


@app.command(
    name="set",
    help=_(
        "Modifica una o varias configuraciones. Valida automáticamente endpoints de red en tiempo real a menos que se use --force."
    ),
)
@handle_config_errors
def set_configs(
    ctx: typer.Context,
    args: list[str] = typer.Argument(
        ...,
        help=_("Pares de CLAVE VALOR (ej: chat_id 12345 upload_limit_rate_kbps 500)"),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help=_(
            "Fuerza el guardado sin verificar la conectividad de los endpoints de red (modo offline)."
        ),
    ),
):
    settings_name, service = _get_config_tools(ctx)
    updates = service.prepare_updates(args)

    for key, val in updates.items():
        changed, final_val = service.apply_set(settings_name, key, val)
        if changed:
            UI.success(
                _(
                    "Configuración [bold]{key}[/] actualizada con [bold]{value}[/]."
                ).format(key=key, value=final_val)
            )
        else:
            UI.info(
                _("Configuración [bold]{key}[/] ya tiene ese valor.").format(key=key)
            )


@app.command(
    "unset",
    help=_("Quita una configuración personalizada para usar el valor por defecto."),
)
@handle_config_errors
def unset_config(
    ctx: typer.Context,
    key: str = typer.Argument(..., help=_("Clave a restaurar a su valor por defecto")),
):
    settings_name, service = _get_config_tools(ctx)
    default_val = service.restore_default(settings_name, key)
    UI.success(
        _("Restaurado [bold]{key}[/] al valor por defecto: {value}").format(
            key=key, value=default_val
        )
    )


@app.command(
    "add",
    help=_("Agrega valores a una lista (ej. exclude_files)."),
)
def add_config(
    ctx: typer.Context,
    key: str,
    values: list[str] = typer.Argument(
        ..., min=1, callback=lambda x: [validate_item(i) for i in x]
    ),
):
    settings_name, service = _get_config_tools(ctx)
    _command_modify_list(settings_name, service, key, values, "add")


@app.command(
    "remove",
    help=_("Quita valores a una lista (ej. exclude_files)."),
)
def remove_config(
    ctx: typer.Context,
    key: str,
    values: list[str] = typer.Argument(
        ..., min=1, callback=lambda x: [validate_item(i) for i in x]
    ),
):
    settings_name, service = _get_config_tools(ctx)
    _command_modify_list(settings_name, service, key, values, "remove")


@app.command(
    "check",
    help=_("Verifica que el perfil actual esté listo para operar."),
)
@handle_config_errors
def check_config(ctx: typer.Context):
    state: CLIState = ctx.obj
    manager = state.manager

    profile_name = state.get_profile_name(strict=False)
    if not profile_name:
        UI.error(_("No hay un perfil activo o el perfil especificado no existe."))
        raise typer.Exit(1)

    # Verificación de integridad de archivos locales (.json y .session)
    with UI.loading(
        _("Comprobando integridad local del perfil: [bold]{name}[/]").format(
            name=profile_name
        )
    ):
        time.sleep(0.2)
        profile = manager.get_profile(profile_name)

        if not profile:
            UI.error(_("Perfil '{name}' no encontrado.").format(name=profile_name))
            raise typer.Exit(1)

        if not profile.is_trinity:
            UI.error(_("El perfil '{name}' está incompleto.").format(name=profile_name))
            if not profile.has_config:
                UI.info(_("Falta el archivo de configuración (.json)."))
            if not profile.has_session:
                UI.info(_("Falta el archivo de sesión de Telegram (.session)."))
            raise typer.Exit(1)

    # Verificación de la Base de Datos SQLite local y Esquema
    with UI.loading(_("Verificando conexión y esquema de base de datos...")):
        try:
            with DatabaseSession(manager.database_path, auto_init_schema=False) as db:
                report = inspect_database(db)

                if report.state == DatabaseState.UP_TO_DATE:
                    UI.success(
                        _("Base de datos: [bold]SQLite[/] al día (v{version}).").format(
                            version=report.current_version
                        )
                    )
                elif report.state == DatabaseState.OUTDATED:
                    UI.warn(
                        _(
                            "Base de datos en versión anterior detectada (v{version})."
                        ).format(version=report.current_version)
                    )
                    legacy_name = f"{manager.database_path.stem}.legacy_v{report.current_version}.sqlite"
                    UI.info(
                        _(
                            "Al ejecutar subidas, se archivará automáticamente como "
                            "'{archive_name}' y se iniciará una base de datos limpia v{target_version}."
                        ).format(
                            archive_name=legacy_name,
                            target_version=report.target_version,
                        )
                    )
                elif report.state == DatabaseState.FRESH:
                    UI.info(
                        _(
                            "Base de datos: [bold]SQLite[/] nueva (se inicializará en v{version})."
                        ).format(version=report.target_version)
                    )
                elif report.state == DatabaseState.AHEAD:
                    UI.error(
                        _(
                            "Base de datos incompatible: versión v{current} en base de datos, "
                            "pero el cliente solo soporta hasta v{target}."
                        ).format(
                            current=report.current_version,
                            target=report.target_version,
                        )
                    )
                    UI.info(
                        _(
                            "Acción requerida: Actualiza toTelegram (`pip install --upgrade totelegram`)."
                        )
                    )
                    raise typer.Exit(1)
                elif report.state == DatabaseState.CORRUPTED:
                    UI.error(
                        _("Base de datos corrupta o inconsistente: {details}").format(
                            details=report.details
                        )
                    )
                    raise typer.Exit(1)

        except typer.Exit:
            raise
        except Exception as e:
            UI.error(_("Error conectando a la base de datos: {error}").format(error=e))
            raise typer.Exit(1)

    # Verificación de Conexión activa con Telegram
    with state.get_telegram_session(profile_name) as client:
        with UI.loading(_("Verificando conexión y credenciales con Telegram...")):
            try:
                me = client.get_me()
                if not me:
                    UI.error(
                        _("No se pudo obtener la información de la cuenta en Telegram.")
                    )
                    raise typer.Exit(1)
            except Exception as e:
                UI.error(
                    _(
                        "Error de conexión con los servidores de Telegram: {error}"
                    ).format(error=e)
                )
                raise typer.Exit(1)

        tier_badge = (
            "[bold cyan]Premium ★[/]"
            if getattr(me, "is_premium", False)
            else _("Estándar")
        )
        user_name = me.first_name or me.username or _("Usuario")

        UI.success(
            _("Conexión con Telegram exitosa: [bold]{user}[/] ({tier})").format(
                user=user_name, tier=tier_badge
            )
        )
        UI.success(
            _("Perfil '[bold green]{name}[/]' verificado y listo para operar.").format(
                name=profile_name
            )
        )
        UI.print(
            _(
                "[dim]Nota: Para subir o respaldar archivos recuerda indicar el destino con [bold]-t / --target[/].[/dim]"
            ),
            spacing="top",
        )
