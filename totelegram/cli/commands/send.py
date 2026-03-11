from pathlib import Path
from typing import List

import typer

from totelegram.cli.commands.config import (
    _get_config_tools,
    handle_config_errors,
)
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


@handle_config_errors
def send_files(
    ctx: typer.Context,
    paths: List[Path] = typer.Argument(
        ...,
        exists=True,
        help="Lista de archivos o carpetas a enviar de forma individual.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Fuerza ignorando el estado del archivo en el sistema",
    ),
):
    """
    Envía archivos a Telegram. Si recibe una carpeta, envía su contenido (recursivo)
    como archivos individuales.
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
    with console.status(f"[dim]Scaneando {len(paths)} archivos[/dim]"):
        scan_report = InventoryEngine(settings, force).scan_granular(paths)

    DisplayUpload.show_skip_report(scan_report, "archivo")

    candidates = scan_report.found
    if not scan_report.found:
        UI.warn("No se encontraron archivos para enviar.")
        UI.print(
            "[dim]Asegúrate de que las rutas existan y no estén excluidas por tus patrones de configuración.[/]"
        )
        raise typer.Exit(0)

    # --- Subida de lo encontrado ---

    with state.scope() as (client, db):
        u_ctx = prepare_upload_context(client, db, settings)
        uploader = UploadService(u_ctx)

        user = u_ctx.owner.first_name or u_ctx.owner.username
        chat_n = u_ctx.tg_chat.title or u_ctx.tg_chat.username
        UI.success(f"Conectado como [bold]{user}[/]")
        UI.info(f"Destino: [bold cyan]{chat_n}[/] [dim](ID: {u_ctx.tg_chat.id})[/]")
        UI.print("", indent=False)

        for path in candidates:
            UI.separator()
            job = get_or_create_job(path, u_ctx, force)

            report = u_ctx.discovery.investigate(job)
            if report.state == AvailabilityState.FULFILLED:
                UI.info(f"{path.name=} ya está disponible en Telegram.")
                if job.status != JobStatus.UPLOADED:
                    job.set_uploaded()
            elif report.state == AvailabilityState.NEEDS_UPLOAD:
                uploader.execute_physical_upload(job, path)
            elif report.state == AvailabilityState.CAN_FORWARD:
                uploader.execute_smart_forward(job, report)
            else:
                raise ValueError(f"Invalid state: {report.state}")

            SnapshotService.generate_snapshot(job)
            UI.success(f"Enviado: [bold]{path.name}[/]")
