from pathlib import Path
from typing import List

import typer

from totelegram.cli.commands.config import _get_config_tools, handle_config_errors
from totelegram.cli.logic import InventoryEngine, prepare_upload_context
from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, DisplayUpload, console
from totelegram.concurrency import AccountBusyError
from totelegram.engine.pipeline import JobPipeline


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
):
    """
    Envía archivos a Telegram. Si recibe una carpeta, envía su contenido (recursivo)
    como archivos individuales.
    """
    state: CLIState = ctx.obj
    profile_name, _ = _get_config_tools(ctx)
    settings = state.manager.get_settings(profile_name)

    if force:
        UI.warn("Forzando la subida de archivos sin comprobar el estado previo.")

    with state.scope() as (client, db):
        u_ctx = prepare_upload_context(
            state=state,
            client=client,
            db=db,
            settings=settings,
            target_override=target,
        )
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

        if not settings.has_custom_app:
            UI.warn("Cuenta en modo público compartido (my.telegram.org recomendado).")

        UI.info(f"Destino: [bold cyan]{chat_n}[/] [dim](ID: {u_ctx.tg_chat.id})[/]")
        UI.print("", indent=False)

        # Escaneamos filtrando por el chat destino resuelto
        with console.status(f"[dim]Escaneando {len(paths)} rutas...[/dim]"):
            scan_report = InventoryEngine(
                settings=settings,
                force=force,
                target_chat_id=u_ctx.tg_chat.id,
            ).scan_granular(paths)

        DisplayUpload.show_skip_report(scan_report, "archivo")

        candidates = scan_report.found
        if not candidates:
            UI.warn("No se encontraron archivos pendientes para este destino.")
            raise typer.Exit(0)

        # Procesamiento de los archivos candidatos
        for idx, path in enumerate(candidates, 1):
            is_last = idx == len(candidates)
            UI.separator()

            try:
                result = pipeline.process(
                    path=path,
                    is_last_in_batch=is_last,
                    force=force,
                )
                if result.is_completed:
                    UI.success(f"Archivo [bold]{path.name}[/] procesado exitosamente.")
                else:
                    UI.info(
                        f"Procesamiento parcial de [bold]{path.name}[/]: {result.message}"
                    )

            except AccountBusyError as e:
                UI.error(str(e))
                raise typer.Exit(1)
            except Exception as e:
                UI.error(f"Error procesando {path.name}: {e}")
                raise typer.Exit(1)
