from pathlib import Path
from typing import TYPE_CHECKING, cast

import tartape
import typer
from rich.console import Console

from totelegram.cli.commands.config import _get_config_tools
from totelegram.cli.ui.console import UI
from totelegram.common.consts import VALUE_NOT_SET, Commands
from totelegram.common.schemas import CLIState
from totelegram.logic.chunker import ChunkingService
from totelegram.logic.discovery import DiscoveryService
from totelegram.logic.snapshot import SnapshotService
from totelegram.logic.uploader import UploadService
from totelegram.manager.database import DatabaseSession
from totelegram.manager.models import Job, SourceFile, TelegramChat
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

        exclusion_patterns = settings.all_exclusion_patterns()
        if not tartape.exists(folder_path):
            with UI.loading("Generando cinta..."):
                tape = tartape.create(
                    folder_path,
                    exclude=exclusion_patterns,
                    calculate_hashes=True,
                )
                try:
                    source = SourceFile.from_tape(folder_path, tape)
                except Exception as e:
                    tape.destroy()
                    raise
        else:
            tape = tartape.open(folder_path)
            source = SourceFile.get(SourceFile.md5sum == tape.fingerprint)
            if not tape.verify():
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
            uploader._execute_physical_upload(job)
        else:
            uploader.run(job, discovery_report)

        SnapshotService.generate_snapshot(job)
        UI.success("Proceso de archivado finalizado correctamente.")
