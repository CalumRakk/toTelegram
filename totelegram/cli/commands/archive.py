from pathlib import Path
from typing import TYPE_CHECKING, cast

import typer
from rich.console import Console

from totelegram.cli.commands.config import _get_config_tools
from totelegram.cli.ui.console import UI
from totelegram.common.consts import VALUE_NOT_SET, Commands
from totelegram.common.enums import AvailabilityState
from totelegram.common.schemas import CLIState
from totelegram.logic.archive_archive import ArchiveService
from totelegram.logic.chunker import ChunkingService
from totelegram.logic.discovery import DiscoveryService
from totelegram.logic.snapshot import SnapshotService
from totelegram.logic.uploader import UploadService
from totelegram.manager.database import DatabaseSession
from totelegram.manager.models import Job, TelegramChat
from totelegram.telegram.client import TelegramSession

if TYPE_CHECKING:
    from pyrogram.types import Chat, User

console = Console()
app = typer.Typer(help="Comandos para archivado de carpetas (Modo Cinta).")


@app.command("folder")
def archive_folder(
    ctx: typer.Context,
    folder_path: Path = typer.Argument(
        ..., exists=True, file_okay=False, dir_okay=True, help="Carpeta a archivar."
    ),
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
        f"\n[bold cyan]Iniciando Operación de Archivado:[/bold cyan] {folder_path.name}\n"
    )

    with DatabaseSession(state.manager.database_path), TelegramSession.from_profile(
        profile_name, state.manager
    ) as client:

        with UI.loading("Sincronizando con Telegram..."):
            tg_chat = cast("Chat", client.get_chat(settings.chat_id))
            me = cast("User", client.get_me())

        UI.success(f"Conectado como [bold]{me.first_name or me.username}[/]")
        UI.info(f"Destino: [bold cyan]{tg_chat.title}[/] [dim](ID: {tg_chat.id})[/dim]")

        archive_service = ArchiveService()
        inventories_dir = state.manager.inventories_dir
        exclusion_patterns = settings.all_exclusion_patterns()
        with UI.loading("Analizando estructura de la carpeta..."):
            source, is_resume, is_modified = archive_service.get_or_create_session(
                folder_path, inventories_dir, exclusion_patterns
            )

        if is_resume:
            if is_modified:
                UI.error("¡Cinta Comprometida! La carpeta ha sido modificada.")
                UI.info(f"Ruta: [dim]{folder_path}[/dim]")
                UI.warn(
                    "Para garantizar la integridad, no se puede reanudar una cinta alterada."
                )
                UI.tip(
                    "Si deseas archivar la nueva versión, debes eliminar el rastro anterior:",
                    commands=f"totelegram profile delete-source (proximamente) o limpiar la DB.",
                )
                raise typer.Exit(1)
            else:
                UI.success(
                    "Estructura válida detectada. Reanudando proceso existente..."
                )
        else:
            UI.success(f"Nueva carpeta detectada. Firma: [bold]{source.md5sum}[/]")

        chat_db, _ = TelegramChat.get_or_create_from_chat(tg_chat)
        job = Job.get_for_source_in_chat(source, chat_db)
        if not job:
            tg_limit = (
                settings.tg_max_size_premium
                if me.is_premium
                else settings.tg_max_size_normal
            )
            job = Job.create_contract(source, chat_db, me.is_premium, tg_limit)
            UI.info(
                f"Contrato de archivado creado (Límite: {tg_limit / (1024*1024):.0f}MB por vol)."
            )

        chunker = ChunkingService(work_dir=state.manager.worktable)
        discovery = DiscoveryService(client)

        if job.payloads.count() == 0:
            chunker.process_job(job)

        uploader = UploadService(
            client=client,
            chunk_service=chunker,
            upload_limit_rate_kbps=settings.upload_limit_rate_kbps,
            max_filename_length=settings.MAX_FILENAME_LENGTH,
            discovery=discovery,
        )

        discovery_report = discovery.investigate(job)

        if force:
            UI.warn("Subida física forzada (--force).")
            uploader.execute_upload_strategy(job)
        else:
            if discovery_report.state == AvailabilityState.SYSTEM_NEW:
                UI.info("Iniciando subida de nuevos volúmenes...")
                uploader.execute_upload_strategy(job)

            elif discovery_report.state == AvailabilityState.FULFILLED:
                UI.success(
                    "¡Operación completada! Todos los volúmenes ya están en el destino."
                )
                job.set_uploaded()

            elif discovery_report.state in [
                AvailabilityState.REMOTE_MIRROR,
                AvailabilityState.REMOTE_PUZZLE,
            ]:
                UI.info(
                    "Carpeta encontrada en el ecosistema. Clonando volúmenes (Smart Forward)..."
                )
                if discovery_report.remotes:
                    uploader.execute_smart_forward(job, discovery_report.remotes)
                else:
                    UI.error("Error al localizar las piezas en la red.")

            elif discovery_report.state == AvailabilityState.REMOTE_RESTRICTED:
                UI.warn(
                    "Se detectaron registros pero los archivos no son accesibles. Re-subiendo..."
                )
                uploader.execute_physical_upload(job)

        # Generar Snapshot final
        SnapshotService.generate_snapshot(job)
        UI.success("Proceso de archivado finalizado correctamente.")
