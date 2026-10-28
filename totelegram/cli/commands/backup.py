from pathlib import Path
from typing import List

import typer

from totelegram.cli.commands.config import handle_config_errors
from totelegram.cli.logic import run_upload_flow


@handle_config_errors
def backup_folders(
    ctx: typer.Context,
    paths: List[Path] = typer.Argument(
        ...,
        exists=True,
        help="Lista de carpetas a archivar",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        "-t",
        help="Nombre, @username o ID del chat destino para este backup (sobrescribe el destino por defecto).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Fuerza ignorando el estado del archivo en el sistema",
    ),
    auto_truncate: bool | None = typer.Option(
        None,
        "--auto-truncate/--no-auto-truncate",
        help="Activa o desactiva la truncación de nombres largos. Sobrescribe la configuración global.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Omite confirmaciones interactivas (ej. al purgar piezas previas con --force).",
    ),
):
    """
    Convierte una carpeta en una Cinta de Datos (TAR) y la distribuye en volúmenes.
    """
    run_upload_flow(
        ctx=ctx,
        paths=paths,
        target=target,
        force=force,
        auto_truncate=auto_truncate,
        yes=yes,
        is_backup=True,
    )
