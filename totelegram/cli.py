import typer

from totelegram.commands import profile, upload
from totelegram.console import console

app = typer.Typer(
    help="Herramienta para subir archivos a Telegram sin límite de tamaño.",
    add_completion=False,
    no_args_is_help=True,
)

app.add_typer(profile.app, name="profile")
app.add_typer(upload.app, name="upload")


@app.callback()
def main(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Mostrar logs detallados"
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
    app()


if __name__ == "__main__":
    app()
