from pathlib import Path
from typing import List

import typer

from totelegram.cli.commands.config import handle_config_errors
from totelegram.cli.logic import run_upload_flow


@handle_config_errors
def send_files(
    ctx: typer.Context,
    paths: List[Path] = typer.Argument(
        ...,
        exists=True,
        help="Lista de archivos o carpetas a enviar de forma individual.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        "-t",
        help="Nombre, @username o ID del chat destino para esta subida (sobrescribe el destino por defecto).",
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
