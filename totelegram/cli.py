import logging
import os
from decimal import __version__
from typing import Optional

import typer

logging.getLogger("dotenv").setLevel(logging.CRITICAL)

from totelegram.commands import config, profile, upload
from totelegram.console import UI, console
from totelegram.core.registry import ProfileManager
from totelegram.logging_config import setup_logging

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
    # verbose: bool = typer.Option(
    #     False, "--verbose", "-v", help="Mostrar logs detallados"
    # ),
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
        False, "--debug", help="Activa el modo debug (DB independiente y logs detallados)."
    )
):
    """
    Callback principal. Se ejecuta antes que cualquier comando.
    Útil para configurar logging global.
    """
    is_debug = debug or os.getenv("TOTELEGRAM_DEBUG") == "1"


    # Si ctx.obj ya existe (porque lo inyectamos en un test), lo usamos.
    # Si no, creamos el de producción.
    if ctx.obj is None:
        ctx.obj = ProfileManager()

    pm = ctx.obj

    if use:
        if not pm.exists(use):
            UI.error(f"El perfil '[bold]{use}[/]' no existe.")
            profile.list_profiles(pm, quiet=True)
            raise typer.Exit(code=1)

        pm.set_override(use)

        if ctx.invoked_subcommand in COMMANDS_IGNORING_USE:
            console.print(
                f"[dim yellow]Nota: El flag --use '{use}' no tiene efecto en comandos de '{ctx.invoked_subcommand}'.[/dim yellow]"
            )
    if is_debug:

        setup_logging("debug_execution.log", logging.DEBUG)
        console.print("[bold yellow]MODO DEBUG ACTIVADO[/] (Usando base de datos de pruebas)")

    ctx.obj = pm


def run_script():
    """Entrada para setup.py"""
    try:
        app()
    except Exception as e:
        UI.error(str(e))
        raise e


if __name__ == "__main__":
    app()
