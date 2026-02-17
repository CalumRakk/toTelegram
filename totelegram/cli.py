import logging
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

    # Si ctx.obj ya existe (porque lo inyectamos en un test), lo usamos.
    # Si no, creamos el de producción.
    if ctx.obj is None:
        ctx.obj = ProfileManager()

    pm : ProfileManager = ctx.obj
    if debug:
        debug_profile = pm.setup_debug_context(use)

        setup_logging("debug_execution.log", logging.DEBUG)
        console.print(f"\n[bold yellow]MODO DEBUG ACTIVADO[/]")
        console.print(f"[dim yellow]Perfil Shadow:[/][white] {debug_profile}\n")

    elif use:
        pm.set_override(use)

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
