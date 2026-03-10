import time
from pathlib import Path
from typing import List

import typer
from rich.rule import Rule

from totelegram.cli.commands.config import _get_config_tools
from totelegram.cli.logic import (
    InventoryEngine,
    get_or_create_job,
    prepare_upload_context,
)
from totelegram.cli.ui import UI, DisplayUpload, console
from totelegram.packaging import SnapshotService
from totelegram.schemas import (
    VALUE_NOT_SET,
    AvailabilityState,
    CLIState,
    Commands,
    JobStatus,
)
from totelegram.uploader import UploadService


def backup_folders(
    ctx: typer.Context,
    paths: List[Path] = typer.Argument(
        ...,
        exists=True,
        help="Lista de carpetas a archivar",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Fuerza ignorando el estado del archivo en el sistema",
    ),
):
    """
    Convierte una carpeta en una Cinta de Datos (TAR) y la distribuye en volúmenes.
    """
    state: CLIState = ctx.obj
    profile_name, _ = _get_config_tools(ctx)

    settings = state.manager.get_settings(profile_name)

    if settings.chat_id == VALUE_NOT_SET:
        UI.error("El chat destino no está configurado.")
        commands = [
            f"{Commands.CONFIG_SET} chat_id <ID>",
            f"{Commands.CONFIG_SEARCH} <QUERY>",
        ]
        UI.tip("puedes configurarlo usando uno de estos comandos:", commands)
        raise typer.Exit(1)

    if force:
        UI.warn("Forzando la subida de carpetas sin comprobar el estado del archivo.")

    # --- Scaneo y Informe ---
    with console.status("[dim]Escaneando directorios...[/]"):
        scan_report = InventoryEngine(settings, force).scan_backup_inventory(paths)

    DisplayUpload.show_skip_report(scan_report, "carpeta", force_verbose=False)

    candidates = scan_report.found
    if not candidates:
        UI.warn("No se encontraron carpetas válidas para procesar.")
        UI.print(
            "[dim]Asegúrate de que las rutas existan y no estén excluidas por tus patrones de configuración.[/]"
        )
        raise typer.Exit(0)

    # --- Subida de lo encontrado ---

    console.print(Rule(style="bright_black"))

    with state.scope() as (client, db):
        u_ctx = prepare_upload_context(client, db, settings)
        uploader = UploadService(u_ctx)

        user = u_ctx.owner.first_name or u_ctx.owner.username
        chat_n = u_ctx.tg_chat.title or u_ctx.tg_chat.username
        UI.success(f"Conectado como [bold]{user}[/]")
        UI.info(f"Destino: [bold cyan]{chat_n}[/] [dim](ID: {u_ctx.tg_chat.id})[/]")
        UI.print("", indent=False)

        for index, folder in enumerate(candidates, 1):
            DisplayUpload.show_backup_header(folder.name, index, len(candidates))

            with UI.loading("Analizando contenido..."):
                report_internal = InventoryEngine(settings).scan_backup_internal(folder)
                time.sleep(0.3)

            DisplayUpload.show_internal_scan_result(report_internal)

            job = get_or_create_job(path=folder, u_ctx=u_ctx, force=force)
            report = u_ctx.discovery.investigate(job)
            if report.state == AvailabilityState.FULFILLED:
                if job.status != JobStatus.UPLOADED:
                    job.set_uploaded()
            elif report.state == AvailabilityState.NEEDS_UPLOAD:
                uploader.execute_physical_upload(job, folder)
            elif report.state == AvailabilityState.CAN_FORWARD:
                uploader.execute_smart_forward(job, report)
            else:
                raise ValueError(f"Invalid state: {report.state}")

            SnapshotService.generate_snapshot(job)
            UI.success("Carpeta archivada.")

        if scan_report:
            console.print(Rule(style="bright_black"))
            DisplayUpload.show_integrity_advice(scan_report)
