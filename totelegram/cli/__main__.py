import logging
import re
import sys
from datetime import datetime, timezone

from totelegram import __VERSION__
from totelegram.common.files import get_user_config_dir
from totelegram.i18n import _, setup_i18n
from totelegram.identity import SettingsManager
from totelegram.schemas import APP_NAME

logging.getLogger("dotenv").setLevel(logging.CRITICAL)
logger = logging.getLogger(__name__)


def _detect_requested_profile() -> str | None:
    """Extrae el perfil solicitado por --use o -u de sys.argv antes de procesar el CLI."""
    for i, arg in enumerate(sys.argv[1:], start=1):
        if arg in ("-u", "--use") and i < len(sys.argv) - 1:
            val = sys.argv[i + 1]
            if not val.startswith("-"):
                return val
        if arg.startswith("--use="):
            return arg.split("=", 1)[1]
    return None


def _bootstrap_i18n() -> str:
    """Inicializa tempranamente el catálogo i18n según el perfil activo o argumento --use."""
    try:
        worktable = get_user_config_dir(APP_NAME)
        manager = SettingsManager(worktable)
        profile_name = _detect_requested_profile() or manager.get_active_profile_name()
        forced_lang = "auto"
        if profile_name and manager.settings_exists(profile_name):
            try:
                profile_settings = manager.get_settings(profile_name)
                forced_lang = profile_settings.language
            except Exception:  # noqa: S110
                pass
        setup_i18n(forced_lang)
        return forced_lang
    except Exception:
        setup_i18n("auto")
        return "auto"


# EJECUCIÓN TEMPRANA DE I18N
# Debe ejecutarse ANTES de importar los comandos para que los valores por defecto
# de typer.Argument(help=...) y typer.Option(help=...) se evalúen con el idioma del perfil activo.
_bootstrap_i18n()

# AHORA SÍ: Importar typer y los comandos con el catálogo ya configurado en el idioma correcto
import typer

from totelegram.cli.commands import (
    auth,
    backup,
    config,
    download,
    forward,
    index,
    prune,
    send,
    snapshot,
    target,
)
from totelegram.cli.state import CLIState
from totelegram.cli.ui import console
from totelegram.logging_config import setup_logging


def version_callback(value: bool):
    if value:
        console.print(f"toTelegram [bold cyan]v{__VERSION__}[/bold cyan]")
        raise typer.Exit()


def create_app() -> typer.Typer:
    """Construye y registra todos los comandos con soporte i18n dinámico."""
    app = typer.Typer(
        help=_("Herramienta para subir archivos a Telegram sin límite de tamaño."),
        add_completion=False,
        no_args_is_help=True,
    )

    app.add_typer(config.app, name="config")
    app.add_typer(auth.app, name="auth")
    app.command(
        name="send",
        help=_(
            "Envía archivos a Telegram. Si recibe una carpeta, envía su contenido (recursivo) como archivos individuales."
        ),
    )(send.send_files)
    app.add_typer(snapshot.app, name="snapshot")
    app.command(
        name="backup",
        help=_(
            "Convierte una carpeta en una Cinta de Datos (TAR) y la distribuye en volúmenes."
        ),
    )(backup.backup_folders)
    app.command(
        name="download",
        help=_(
            "Descarga y restaura un archivo o carpeta archivada a partir de su Snapshot (.json.xz o .ttape)."
        ),
    )(download.download_snapshot)
    app.command(
        name="index",
        help=_(
            "Sincroniza e indexa en la base de datos local los archivos contenidos en cintas (.ttape) de Telegram."
        ),
    )(index.index_tapes)
    app.command(
        name="target",
        help=_(
            "Inspecciona y diagnostica destinos de Telegram. Verifica permisos de subida, capacidades de tamaño y resuelve nombres ambiguos."
        ),
    )(target.manage_target)
    app.command(
        name="forward",
        help=_(
            "Reenvía/replica un archivo o carpeta ya respaldada hacia otro chat de Telegram de forma instantánea y sin consumir ancho de banda de subida (0 bytes transferidos desde tu PC)."
        ),
    )(forward.forward_snapshot)
    app.command(
        name="prune",
        help=_(
            "Libera espacio en disco eliminando de forma segura los archivos locales que ya han sido respaldados por completo en Telegram, conservando el registro en la base de datos local."
        ),
    )(prune.prune_files)
    app.command(
        name="clean",
        help=_(
            "Libera espacio en disco eliminando de forma segura los archivos locales que ya han sido respaldados por completo en Telegram, conservando el registro en la base de datos local."
        ),
    )(prune.prune_files)

    # ATAJOS
    app.command(
        name="login",
        help=_(
            "Inicia sesión con tu cuenta de Telegram de forma guiada e interactiva. Configura y activa automáticamente la sesión para su uso inmediato."
        ),
    )(auth.login_command)
    app.command(
        name="logout",
        help=_(
            "Cierra la sesión y elimina los archivos de credenciales del perfil. Por defecto, revoca la sesión remota en Telegram."
        ),
    )(auth.logout_command)
    app.command(
        name="status",
        help=_(
            "Muestra el estado de la cuenta activa o audita los archivos respaldados de una carpeta local."
        ),
    )(auth.status_command)
    app.command(
        name="whoami",
        help=_(
            "Muestra el estado de la cuenta activa o audita los archivos respaldados de una carpeta local."
        ),
    )(auth.status_command)
    app.command(
        name="switch",
        help=_("Cambia la cuenta activa del sistema entre las cuentas guardadas."),
    )(auth.switch_command)

    @app.callback()
    def main(
        ctx: typer.Context,
        use: str | None = typer.Option(
            None,
            "--use",
            "-u",
            help=_("Perfil a utilizar para esta operación (ignora el perfil activo)."),
            is_eager=True,
        ),
        version: bool | None = typer.Option(
            None,
            "--version",
            "-V",
            callback=version_callback,
            is_eager=True,
            help=_("Muestra la versión de la aplicación."),
        ),
        debug: bool = typer.Option(
            False,
            "--debug",
            help=_("Activa el modo de depuración detallado."),
        ),
    ):
        """Se ejecuta antes que cualquier comando."""
        worktable = get_user_config_dir(APP_NAME)
        config_manager = SettingsManager(worktable)

        profile_name = use or config_manager.get_active_profile_name()
        forced_lang = "auto"

        if profile_name and config_manager.settings_exists(profile_name):
            try:
                profile_settings = config_manager.get_settings(profile_name)
                forced_lang = profile_settings.language
            except Exception:  # noqa: S110
                pass

        setup_i18n(forced_lang)

        cmd_name = ctx.invoked_subcommand or "sys"
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")

        log_dir = worktable / "logs"
        log_path = log_dir / f"{timestamp}_{cmd_name}.log"

        setup_logging(log_path, debug)

        ctx.obj = CLIState(
            manager=config_manager, profile_name=profile_name, is_debug=debug
        )
        logger.debug(f"--- Nueva Ejecución: {sys.argv} [Lang: {forced_lang}]")

    return app


app = create_app()


def run_script():
    # Buscamos argumentos que sean exactamente números negativos (IDs de Telegram)
    regex = re.compile(r"^-\d+$")
    if len(sys.argv) > 1:
        sys.argv = [f"ID:{arg}" if regex.match(arg) else arg for arg in sys.argv]

    # Re-sincronización dinámica de idioma con los argumentos definitivos
    _bootstrap_i18n()
    current_app = create_app()

    try:
        current_app()
    except SystemExit:
        pass  # Salidas limpias
    except Exception as e:
        import typer

        from totelegram.exceptions import ToTelegramError

        if isinstance(e, ToTelegramError):
            from totelegram.cli.views.errors import CLIExceptionHandler

            CLIExceptionHandler.render(e)
            sys.exit(e.exit_code)

        if not isinstance(e, typer.Exit):
            import logging

            logging.getLogger("totelegram.crash").exception(
                "Error crítico no controlado:"
            )

        from totelegram.cli.ui import UI

        UI.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    run_script()
