from pathlib import Path
from typing import TYPE_CHECKING, List, cast

import typer

from totelegram.cli.commands.config import _get_config_tools, handle_config_errors
from totelegram.cli.ui.console import UI, console
from totelegram.cli.ui.views import DisplayUpload
from totelegram.common.consts import VALUE_NOT_SET, Commands
from totelegram.common.enums import AvailabilityState, Strategy
from totelegram.common.schemas import CLIState, ScanReport
from totelegram.common.utils import is_excluded
from totelegram.logic.chunker import ChunkingService
from totelegram.logic.discovery import DiscoveryService
from totelegram.logic.snapshot import SnapshotService
from totelegram.logic.uploader import UploadService
from totelegram.manager.database import DatabaseSession
from totelegram.manager.models import Job, SourceFile, TelegramChat
from totelegram.telegram.client import TelegramSession

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Chat, User


def _scan_and_filter(
    target: Path | List[Path], patterns: list[str], max_filesize_bytes: int
) -> ScanReport:
    """
    Escanea el objetivo y aplica reglas de exclusión.
    Retorna un ScanReport con los archivos válidos y las omisiones categorizadas.
    """
    if not isinstance(target, list) and not isinstance(target, Path):
        raise ValueError(f"Invalid target type: {type(target)}")

    report = ScanReport()

    def has_snapshot(file_path: Path) -> bool:
        filename_plus_ext = file_path.with_name(f"{file_path.name}.json.xz")
        stem_plus_ext = file_path.with_name(f"{file_path.stem}.json.xz")
        return filename_plus_ext.exists() or stem_plus_ext.exists()

    def process_candidate(p: Path):
        # report.total_scanned += 1
        # if p.name.endswith(".json.xz"):
        #     report.snapshots_found += 1

        # Exclusión por Patrón (user config)
        if is_excluded(p, patterns):
            report.skipped_by_exclusion.append(p)
            return

        # Exclusión por Snapshot
        if has_snapshot(p):
            report.skipped_by_snapshot.append(p)
            return

        # Exclusión por Tamaño
        if p.stat().st_size > max_filesize_bytes:
            report.skipped_by_size.append(p)
            return

        # Si pasa todo, es un archivo válido
        report.found.append(p)

    if isinstance(target, list):
        candidates = target
    elif isinstance(target, Path):
        if target.is_file():
            candidates = [target]
        else:
            candidates = list(target.rglob("*"))
    else:
        raise ValueError(f"Invalid target type: {type(target)}")

    for p in candidates:
        if p.is_file():
            process_candidate(p)

    return report


@handle_config_errors
def upload_file(
    ctx: typer.Context,
    target: Path = typer.Argument(
        ..., exists=True, help="Archivo o directorio a procesar."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Ignora la red y fuerza la subida física de los bytes.",
    ),
):
    """
    Sube archivos o archivos de un directorio a Telegram.
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

    exclusion_patterns = settings.all_exclusion_patterns()
    with console.status(f"[dim]Escaneando {target}...[/dim]"):
        scan_report = _scan_and_filter(
            target, exclusion_patterns, settings.max_filesize_bytes
        )

    if target.is_dir():
        # Si es una carpeta, mostramos lo que encontro.
        DisplayUpload.announces_total_files_found(scan_report)

    # Reporta la exclusion de un archivo o miles.
    verbose = False if scan_report.total_files > 7 else True
    DisplayUpload.show_skip_report(
        scan_report,
        verbose,
    )

    # Debe ir despues del reporte de exclusion.
    if not scan_report.found:
        if target.is_dir():
            UI.warn("No se encontraron archivos válidos para procesar.")
        raise typer.Exit(0)

    UI.info(f"[dim]Procesando {len(scan_report.found)} archivos[/dim]")

    with DatabaseSession(state.manager.database_path), TelegramSession.from_profile(
        profile_name, state.manager
    ) as client:

        with UI.loading("Sincronizando con Telegram..."):
            tg_chat = cast("Chat", client.get_chat(settings.chat_id))
            me = cast("User", client.get_me())

        UI.success(f"Conectado como [bold]{me.first_name or me.username}[/]")
        UI.info(
            f"Destino: [bold cyan]{tg_chat.title=}[/] [dim](ID: {tg_chat.id})[/dim]"
        )
        chunker = ChunkingService(work_dir=state.manager.worktable)
        discovery = DiscoveryService(client)
        uploader = UploadService(
            client=client,
            chunk_service=chunker,
            upload_limit_rate_kbps=settings.upload_limit_rate_kbps,
            max_filename_length=settings.MAX_FILENAME_LENGTH,
            discovery=discovery,
        )
        for path in scan_report.found:
            with console.status(f"[dim]Procesando {path}...[/dim]"):
                source = SourceFile.get_or_create_from_path(path)

            chat_db, _ = TelegramChat.get_or_create_from_chat(tg_chat)
            job = Job.get_for_source_in_chat(source, chat_db)
            if not job:
                tg_limit = (
                    settings.tg_max_size_premium
                    if me.is_premium
                    else settings.tg_max_size_premium
                )
                job = Job.create_contract(source, chat_db, me.is_premium, tg_limit)
                UI.info(f"Estrategia fijada: [bold]{job.strategy.value}[/]")
                if job.strategy == Strategy.SINGLE:
                    UI.info(f"[bold]Archivo único:[/bold] [blue]{path.name}[/blue]")
                else:
                    parts_count = discovery._get_expected_count(job)
                    UI.info(
                        f"[bold]Fragmentando en {parts_count} partes:[/bold] [blue]{path.name}[/blue]"
                    )

            discovery_report = discovery.investigate(job)
            if force:
                UI.warn(f"Subida física forzada: [dim]{path.name}[/]")
                uploader.execute_physical_upload(job)
                SnapshotService.generate_snapshot(job)
                continue

            if discovery_report.state == AvailabilityState.SYSTEM_NEW:
                UI.info(f"Subiendo nuevo: [dim]{path.name}[/]")
                uploader.execute_physical_upload(job)

            elif discovery_report.state == AvailabilityState.FULFILLED:
                UI.info(f"Omitido (Ya disponible): [dim]{path.name}[/]")
                job.set_uploaded()

            elif discovery_report.state in [
                AvailabilityState.REMOTE_MIRROR,
                AvailabilityState.REMOTE_PUZZLE,
            ]:
                UI.info(f"Clonando desde la red (Smart Forward): [dim]{path.name}[/]")
                if discovery_report.remotes:
                    uploader.execute_smart_forward(job, discovery_report.remotes)
                else:
                    UI.error("No se encontraron remotes para clonar.")
            elif discovery_report.state == AvailabilityState.REMOTE_RESTRICTED:
                UI.warn(f"Enlace roto en la red. Re-subiendo: [dim]{path.name}[/]")
                uploader.execute_physical_upload(job)

            SnapshotService.generate_snapshot(job)
