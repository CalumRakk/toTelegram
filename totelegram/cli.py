from decimal import __version__
from typing import Optional

import typer

from totelegram.commands import profile, upload
from totelegram.console import console

app = typer.Typer(
    help="Herramienta para subir archivos a Telegram sin límite de tamaño.",
    add_completion=False,
    no_args_is_help=True,
)

app.add_typer(profile.app, name="profile")
app.command(name="upload")(upload.upload_file)


def version_callback(value: bool):
    if value:
        console.print(f"toTelegram [bold cyan]v{__version__}[/bold cyan]")
        raise typer.Exit()


@app.callback()
def main(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Mostrar logs detallados"
    ),
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        callback=version_callback,
        is_eager=True,
        help="Muestra la versión de la aplicación.",
    ),
):
    """
    Callback principal. Se ejecuta antes que cualquier comando.
    Útil para configurar logging global.
    """
    if verbose:
        console.print("[dim]Modo verbose activado[/dim]")


def run_script():
    """Entrada para setup.py"""
    try:
        app()
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise e


if __name__ == "__main__":
    app()
