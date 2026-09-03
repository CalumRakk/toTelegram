import time
from pathlib import Path
from typing import List, Optional

import typer

from totelegram.cli.commands.config import _get_config_tools
from totelegram.cli.logic import InventoryEngine, prepare_upload_context
from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, DisplayUpload, console
from totelegram.concurrency import AccountBusyError
from totelegram.engine.pipeline import JobPipeline
from totelegram.engine.planner import PathLengthExceededError
from totelegram.schemas import VALUE_NOT_SET, Commands


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
    auto_truncate: Optional[bool] = typer.Option(
        None,
        "--auto-truncate/--no-auto-truncate",
        help="Activa o desactiva la truncación de nombres largos. Sobrescribe la configuración global.",
    ),
):
    """
    Convierte una carpeta en una Cinta de Datos (TAR) y la distribuye en volúmenes.
    """
    state: CLIState = ctx.obj
    profile_name, _ = _get_config_tools(ctx)
    settings = state.manager.get_settings(profile_name)

    final_auto_truncate = (
        auto_truncate if auto_truncate is not None else settings.auto_truncate
    )

    if settings.chat_id == VALUE_NOT_SET:
        UI.error("El chat destino no está configurado.")
        commands = [
            f"{Commands.CONFIG_SET} chat_id <ID>",
            f"{Commands.CONFIG_SEARCH} <QUERY>",
        ]
        UI.tip("Puedes configurarlo usando uno de estos comandos:", commands)
        raise typer.Exit(1)

    if force:
        UI.warn("Forzando la subida de carpetas sin comprobar el estado del archivo.")

    with console.status("[dim]Escaneando directorios...[/]"):
        scan_report = InventoryEngine(settings, force).scan_backup_inventory(paths)

    DisplayUpload.show_skip_report(scan_report, "carpeta", force_verbose=False)

    candidates = scan_report.found
    if not candidates:
        UI.warn("No se encontraron carpetas válidas para procesar.")
        raise typer.Exit(0)

    UI.separator()

    with state.scope() as (client, db):
        u_ctx = prepare_upload_context(state, client, db, settings)
        pipeline = JobPipeline(u_ctx)

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

            try:
                result = pipeline.process(
                    path=folder,
                    is_last_in_batch=is_last,
                    force=force,
                    auto_truncate=final_auto_truncate,
                )
                if result.is_completed:
                    UI.success(
                        f"Carpeta [bold]{folder.name}[/] procesada exitosamente."
                    )
                else:
                    UI.info(
                        f"Procesamiento parcial de [bold]{folder.name}[/]: {result.message}"
                    )

            except PathLengthExceededError:
                UI.error(f"Error al empaquetar la carpeta: [bold]{folder.name}[/]")
                UI.print(
                    "[dim]El formato TAR tiene un límite estricto para la longitud de los nombres y rutas de archivos.[/dim]"
                )
                UI.print(
                    "[dim]Algunos archivos dentro de esta carpeta superan este límite.[/dim]"
                )
                UI.educational_tip(
                    title="Nombres de archivo demasiado largos",
                    message="Tienes dos opciones para resolver esto:\n"
                    "1. Renombrar manualmente los archivos con rutas largas.\n"
                    "2. Dejar que toTelegram trunque (recorte) los nombres automáticamente al subirlos.",
                    commands=[
                        f'totelegram backup "{folder}" --auto-truncate',
                        "totelegram config set auto_truncate true",
                    ],
                    spacing="block",
                    border_style="yellow",
                )
                raise typer.Exit(1)
            except AccountBusyError as e:
                UI.error(str(e))
                raise typer.Exit(1)
            except Exception as e:
                UI.error(f"Error procesando {folder.name}: {e}")
                raise typer.Exit(1)

        if scan_report.skipped_by_integrity:
            UI.separator()
            DisplayUpload.show_integrity_advice(scan_report)
