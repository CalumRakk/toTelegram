from pathlib import Path
from typing import TYPE_CHECKING

import typer

from totelegram.cli.commands.config import _get_config_tools
from totelegram.cli.commands.send import (
    get_or_create_job,
    prepare_upload_context,
)
from totelegram.cli.ui import UI
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


class ArchiveFilter:
    def __init__(self, settings, ui_report):
        self.settings = settings
        self.ui_report = ui_report

    def __call__(self, path: Path) -> bool:
        """Esta es la función que tartape llamará"""
        if path.suffix == ".xz":
            self.ui_report.log_skip(path, "Es un snapshot")
            return True

        if path.stat().st_size > self.settings.max_filesize_bytes:
            self.ui_report.log_skip(path, "Demasiado grande")
            return True

        return False


def backup_folders(
    ctx: typer.Context,
    folderpath: Path = typer.Argument(
        ..., exists=True, dir_okay=True, help="Carpeta a archivar."
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

    UI.info(
        f"[bold cyan]Iniciando Operación de Archivado:[/bold cyan] {folderpath.name}",
        spacing="block",
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
