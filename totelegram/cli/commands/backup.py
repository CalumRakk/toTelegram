import time
from pathlib import Path
from typing import List

import typer
from rich.rule import Rule

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

    DisplayUpload.show_skip_report(scan_report, "carpeta", force_verbose=False)

    candidates = scan_report.found.copy()
    count_candidates = len(scan_report.found)
    if not candidates:
        UI.warn("No se encontraron carpetas válidas para procesar.")
        UI.print(
            "[dim]Asegúrate de que las rutas existan y no estén excluidas por tus patrones de configuración.[/]"
        )
        raise typer.Exit(0)

    # --- Subida de lo encontrado ---

    # with state.scope() as (client, db):
    console.print(Rule(style="bright_black"))
    for index, folder in enumerate(candidates, 1):
        if index > 1:
            console.print(Rule(style="bright_black"))

        prefix = f"[dim]{index}/{count_candidates}[/] " if count_candidates > 1 else ""
        UI.print(
            f"{prefix}Preparando archivo para: [bold cyan]{folder.name}[/]",
            highlight=False,
            indent=False,
        )

        with UI.loading("Analizando contenido..."):
            report_internal = InventoryEngine(settings).scan_backup_internal(folder)
            time.sleep(0.3)

        if report_internal.total_skipped > 0:
            DisplayUpload.show_skip_report(
                report_internal, "archivo", force_verbose=False, skip_title=True
            )
        else:
            UI.print("[success]>[/] Contenido íntegro y listo.")

    if scan_report:
        console.print(Rule(style="bright_black"))
        DisplayUpload.show_integrity_advice(scan_report)
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
