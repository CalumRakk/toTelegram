from pathlib import Path

import typer

from totelegram.cli.commands.config import handle_config_errors
from totelegram.cli.logic import run_upload_flow


@handle_config_errors
def send_files(
    ctx: typer.Context,
    paths: list[Path] = typer.Argument(
        ...,
        exists=True,
        help="Lista de archivos o carpetas a enviar de forma individual.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        "-t",
        envvar="TOTELEGRAM_TARGET",
        help="Destino en Telegram: ID, @username, 'me' o nombre del canal (o define TOTELEGRAM_TARGET).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Fuerza ignorando el estado del archivo en el sistema",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Omite confirmaciones interactivas (ej. al purgar piezas previas con --force).",
    ),
):
    """
    Envía archivos a Telegram. Si recibe una carpeta, envía su contenido (recursivo)
    como archivos individuales.
    """
    run_upload_flow(
        ctx=ctx,
        paths=paths,
        target=target,
        force=force,
        yes=yes,
        is_backup=False,
    )
