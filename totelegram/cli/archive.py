from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console

from totelegram.cli.config import _get_config_tools
from totelegram.cli.console import UI
from totelegram.cli.upload import get_or_create_job, prepare_upload_context
from totelegram.packaging import SnapshotService
from totelegram.schemas import (
    VALUE_NOT_SET,
    AvailabilityState,
    CLIState,
    Commands,
    JobStatus,
)
from totelegram.uploader import UploadService

if TYPE_CHECKING:
    from pyrogram.types import Chat, User

console = Console()
app = typer.Typer(help="Comandos para archivado de carpetas (Modo Cinta).")


@app.command("folder")
def archive_folder(
    ctx: typer.Context,
    folderpath: Path = typer.Argument(..., exists=True, help="Carpeta a archivar."),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Ignora la red y fuerza la subida física de los bytes.",
    ),
):
    """
    Convierte una carpeta en una Cinta de Datos (TAR) y la distribuye en volúmenes.
    """
    state: CLIState = ctx.obj
    profile_name, service = _get_config_tools(ctx)

    settings = state.manager.get_settings(profile_name)

    if settings.chat_id == VALUE_NOT_SET:
        UI.error("El chat destino no está configurado.")
        commands = [
            f"{Commands.CONFIG_SET} chat_id <ID>",
            f"{Commands.CONFIG_SEARCH} <QUERY>",
        ]
        UI.tip("puedes configurarlo usando uno de estos comandos:", commands)
        raise typer.Exit(1)

    console.print(
        f"\n[bold cyan]Iniciando Operación de Archivado:[/bold cyan] {folderpath.name}\n"
    )

    with state.scope() as (client, db):
        u_ctx = prepare_upload_context(client, db, settings)
        uploader = UploadService(u_ctx)

        job, _ = get_or_create_job(path=folderpath, u_ctx=u_ctx)
        report = u_ctx.discovery.investigate(job)
        if report.state == AvailabilityState.FULFILLED:
            if job.status != JobStatus.UPLOADED:
                job.set_uploaded()
        elif report.state == AvailabilityState.NEEDS_UPLOAD:
            uploader.execute_physical_upload(job, folderpath)
        elif report.state == AvailabilityState.CAN_FORWARD:
            uploader.execute_smart_forward(job, report)
        else:
            raise ValueError(f"Invalid state: {report.state}")

        SnapshotService.generate_snapshot(job)
        UI.success("Proceso de archivado finalizado correctamente.")
