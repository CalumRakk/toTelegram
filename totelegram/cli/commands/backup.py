from pathlib import Path
from typing import List

import typer

from totelegram.cli.commands.config import _get_config_tools
from totelegram.cli.logic import (
    InventoryEngine,
)
from totelegram.cli.ui import UI, DisplayUpload, console
from totelegram.schemas import (
    VALUE_NOT_SET,
    CLIState,
    Commands,
)


def backup_folders(
    ctx: typer.Context,
    paths: List[Path] = typer.Argument(
        ...,
        exists=True,
        help="Lista de carpetas a archivar",
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

    # --- Scaneo y Informe ---
    with console.status(f"[dim]Escaneando...[/dim]"):
        scan_report = InventoryEngine(settings).scan_backup_inventory(paths)

    DisplayUpload.show_skip_report(scan_report, "carpeta")

    candidates = scan_report.found.copy()
    count_candidates = len(scan_report.found)
    if not candidates:
        UI.info("No hay carpetas para enviar.")
        raise typer.Exit(0)

    # --- Subida de lo encontrado ---

    # with state.scope() as (client, db):
    for index, folder in enumerate(candidates, 1):
        count = "" if count_candidates == 1 else f"({index}/{count_candidates}):"
        UI.info(
            f"[dim]{count} Archivando la carpeta: {folder.name}[/]",
            spacing="top",
        )
        with console.status(f"[dim]Escaneando...[/dim]"):
            scan_report = InventoryEngine(settings).scan_backup_internal(folder)

        DisplayUpload.show_skip_report(scan_report, "archivo", force_verbose=False)

        # u_ctx = prepare_upload_context(client, db, settings)
        # uploader = UploadService(u_ctx)

        # job, _ = get_or_create_job(path=folderpath, u_ctx=u_ctx)
        # report = u_ctx.discovery.investigate(job)
        # if report.state == AvailabilityState.FULFILLED:
        #     if job.status != JobStatus.UPLOADED:
        #         job.set_uploaded()
        # elif report.state == AvailabilityState.NEEDS_UPLOAD:
        #     uploader.execute_physical_upload(job, folderpath)
        # elif report.state == AvailabilityState.CAN_FORWARD:
        #     uploader.execute_smart_forward(job, report)
        # else:
        #     raise ValueError(f"Invalid state: {report.state}")

        # SnapshotService.generate_snapshot(job)
        # UI.success("Proceso de archivado finalizado correctamente.")
