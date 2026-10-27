import logging
import re
import sys
from datetime import datetime, timezone

import typer

from totelegram import __VERSION__
from totelegram.cli.commands import (
    auth,
    backup,
    config,
    download,
    index,
    send,
    snapshot,
    target,
)
from totelegram.cli.state import CLIState
from totelegram.cli.ui import console
from totelegram.common.files import get_user_config_dir
from totelegram.identity import SettingsManager
from totelegram.logging_config import setup_logging
from totelegram.schemas import APP_NAME

logging.getLogger("dotenv").setLevel(logging.CRITICAL)

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="Herramienta para subir archivos a Telegram sin límite de tamaño.#",
    add_completion=False,
    no_args_is_help=True,
)

app.add_typer(config.app, name="config")
app.add_typer(auth.app, name="auth")
app.command(name="send")(send.send_files)
app.add_typer(snapshot.app, name="snapshot")
app.command(name="backup")(backup.backup_folders)
app.command(name="download")(download.download_snapshot)
app.command(name="index")(index.index_tapes)
app.command(name="target")(target.manage_target)

# --- ATAJOS ---
app.command(name="login")(auth.login_command)
app.command(name="logout")(auth.logout_command)
app.command(name="status")(auth.status_command)
app.command(name="whoami")(auth.status_command)
app.command(name="switch")(auth.switch_command)


def version_callback(value: bool):
    if value:
        console.print(f"toTelegram [bold cyan]v{__VERSION__}[/bold cyan]")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    use: str | None = typer.Option(
        None,
        "--use",
        "-u",
        help="Perfil a utilizar para esta operación (ignora el perfil activo).",
        is_eager=True,
    ),
    version: bool | None = typer.Option(
        None,
        "--version",
        "-V",
        callback=version_callback,
        is_eager=True,
        help="Muestra la versión de la aplicación.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
    ),
):
    """
    Se ejecuta antes que cualquier comando.
    """
    worktable = get_user_config_dir(APP_NAME)
    config_manager = SettingsManager(worktable)

    cmd_name = ctx.invoked_subcommand or "sys"
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")

    log_dir = worktable / "logs"
    log_path = log_dir / f"{timestamp}_{cmd_name}.log"

    setup_logging(log_path, debug)

    # main resuelve la intencion del nombre del perfil a usar; las validaciones dependen del contexto del comando.
    profile_name = use or config_manager.get_active_profile_name()
    ctx.obj = CLIState(
        manager=config_manager, profile_name=profile_name, is_debug=debug
    )
    logger.debug(f"--- Nueva Ejecución: {sys.argv} ---")


def run_script():
    # Buscamos argumentos que sean exactamente números negativos (IDs de Telegram)
    # "-1001309586477" -> "ID:-1001309586477"
    regex = re.compile(r"^-\d+$")
    if len(sys.argv) > 1:
        sys.argv = [f"ID:{arg}" if regex.match(arg) else arg for arg in sys.argv]

    try:
        from totelegram.cli.__main__ import app

        app()
    except SystemExit:
        pass  # Salidas limpias (ej. sys.exit(0))
    except Exception as e:
        import typer

        if not isinstance(e, typer.Exit):
            import logging

            logging.getLogger("totelegram.crash").exception(
                "Error crítico no controlado:"
            )

        from totelegram.cli.ui import UI

        UI.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    app()
