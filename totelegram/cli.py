import logging
import re
import sys
from decimal import __version__
from typing import Optional

import typer

from totelegram.core.schemas import CLIState

logging.getLogger("dotenv").setLevel(logging.CRITICAL)

from totelegram.commands import config, profile, upload
from totelegram.console import console
from totelegram.core.registry import SettingsManager
from totelegram.logging_config import setup_logging

logger = logging.getLogger(__name__)
COMMANDS_IGNORING_USE = ["profile", "version"]

app = typer.Typer(
    help="Herramienta para subir archivos a Telegram sin límite de tamaño.",
    add_completion=False,
    no_args_is_help=True,
)

app.add_typer(config.app, name="config")
app.add_typer(profile.app, name="profile")
app.command(name="upload")(upload.upload_file)


def version_callback(value: bool):
    if value:
        console.print(f"toTelegram [bold cyan]v{__version__}[/bold cyan]")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    use: Optional[str] = typer.Option(
        None,
        "--use",
        "-u",
        help="Perfil a utilizar para esta operación (ignora el perfil activo).",
        is_eager=True,
    ),
    version: Optional[bool] = typer.Option(
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
        help="Activa el modo debug (DB independiente y logs detallados).",
    ),
):
    """
    Callback principal. Se ejecuta antes que cualquier comando.
    Útil para configurar logging global.
    """

    config_manager = SettingsManager()
    active_profile = use or config_manager.get_active_settings_name()

    if not active_profile:
        logger.info("No se ha especificado ni hay un perfil activo en el sistema.")

    if debug is True:
        setup_logging("debug_execution.log", logging.DEBUG)
        console.print(f"\n[bold yellow]MODO DEBUG ACTIVADO[/]")

    ctx.obj = CLIState(
        manager=config_manager, settings_name=active_profile, is_debug=debug
    )


def run_script():
    # Buscamos argumentos que sean exactamente números negativos (IDs de Telegram)
    # "-1001309586477" -> "ID:-1001309586477"
    regex = re.compile(r"^-?\d+$")
    if len(sys.argv) > 1:
        sys.argv = [f"ID:{arg}" if regex.match(arg) else arg for arg in sys.argv]

    try:
        from totelegram.cli import app

        app()
    except Exception as e:
        from totelegram.console import UI

        UI.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    app()
