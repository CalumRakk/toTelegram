import time
from pathlib import Path
from typing import List

import typer

from totelegram.cli.commands.config import _get_config_tools
from totelegram.cli.logic import (
    InventoryEngine,
    get_or_create_job,
    prepare_upload_context,
)
from totelegram.cli.ui import UI, DisplayUpload, console
from totelegram.schemas import VALUE_NOT_SET, CLIState, Commands
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

    UI.separator()

    with state.scope() as (client, db):
        u_ctx = prepare_upload_context(state, client, db, settings)
        uploader = UploadService(u_ctx)

        from totelegram.telegram.patches import get_patch_status

        status = get_patch_status()
        if status["applied"]:
            UI.success("Core Engine: Pyrogram Runtime Patches [ACTIVE]")
        else:
            UI.error("Core Engine: Pyrogram Runtime Patches [FAILED]")

        user = u_ctx.owner.first_name or u_ctx.owner.username
        chat_n = u_ctx.tg_chat.title or u_ctx.tg_chat.username
        UI.success(f"Conectado como [bold]{user}[/]")
        UI.info(f"Destino: [bold cyan]{chat_n}[/] [dim](ID: {u_ctx.tg_chat.id})[/]")
        UI.print("", indent=False)

        for index, folder in enumerate(candidates, 1):
            is_last = index == len(candidates)
            DisplayUpload.show_backup_header(folder.name, index, len(candidates))

            with UI.loading("Analizando contenido..."):
                report_internal = InventoryEngine(settings).scan_backup_internal(folder)
                time.sleep(0.3)

            DisplayUpload.show_internal_scan_result(report_internal)

            job = get_or_create_job(folder, u_ctx, force, is_last)
            if job is None:
                continue

            if uploader.process_job(job, folder):
                UI.success(f"Carpeta [bold]{folder.name}[/] procesada exitosamente.")

        if scan_report.skipped_by_integrity:
            UI.separator()
            DisplayUpload.show_integrity_advice(scan_report)
