from pathlib import Path
from typing import TYPE_CHECKING, List, Tuple, cast

import typer

from totelegram.cli.commands.config import _get_config_tools, handle_config_errors
from totelegram.cli.ui.console import UI, console
from totelegram.cli.ui.views import DisplayUpload
from totelegram.common.consts import VALUE_NOT_SET, Commands
from totelegram.common.enums import AvailabilityState
from totelegram.common.schemas import CLIState, ScanReport
from totelegram.common.types import UploadContext
from totelegram.common.utils import is_excluded
from totelegram.logic.discovery import DiscoveryService
from totelegram.logic.snapshot import SnapshotService
from totelegram.logic.uploader import UploadService
from totelegram.manager.models import (
    Job,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.manager.setting import Settings

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Chat, User


def get_or_create_job(path: Path, u_ctx: UploadContext) -> Tuple[Job, bool]:
    chat_db, _ = TelegramChat.get_or_create_from_chat(u_ctx.tg_chat)

    if path.is_dir():
        try:
            exclusion_patterns = u_ctx.settings.all_exclusion_patterns()
            with UI.loading("Obteniendo cinta..."):
                source = Source.get_or_create_from_folderpath(path, exclusion_patterns)
        except Exception as e:
            UI.error("¡Cinta Comprometida! La carpeta ha sido modificada.")
            UI.info(f"Ruta: [dim]{path}[/dim]")
            UI.warn(
                "Para garantizar la integridad, no se puede reanudar una cinta alterada."
            )
            UI.tip(
                "Si deseas archivar la nueva versión, debes eliminar el rastro anterior:",
                commands=f"totelegram profile delete-source (proximamente) o limpiar la DB.",
            )
            raise typer.Exit(1)
    else:
        with console.status(f"[dim]Procesando {path}...[/dim]"):
            source = Source.get_or_create_from_filepath(path)

    job = Job.get_for_source_in_chat(source, chat_db)
    if not job:
        tg_limit = (
            u_ctx.settings.tg_max_size_premium
            if u_ctx.owner.is_premium
            else u_ctx.settings.tg_max_size_normal
        )
        job = Job.formalize_intent(source, chat_db, u_ctx.owner.is_premium, tg_limit)
        UI.info(
            f"Nuevo contrato de disponibilidad creado (Límite: {tg_limit / (1024*1024):.0f}MB)"
        )
        return job, True

    return job, False


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


def prepare_upload_context(client: "Client", db, settings: Settings) -> UploadContext:
    """
    Centraliza la inicialización de servicios y validación de red.
    Lanza typer.Exit si algo falla, limpiando el comando principal.
    """
    with UI.loading("Sincronizando con Telegram..."):
        try:
            tg_chat = cast("Chat", client.get_chat(settings.chat_id))
            me = cast("User", client.get_me())
            owner = TelegramUser.get_or_create_from_tg(me)
        except Exception as e:
            UI.error(f"Error de conexión: {e}")
            raise typer.Exit(1)
    discovery = DiscoveryService(client, db)

    UI.success(f"Conectado como [bold]{me.first_name or me.username}[/]")
    UI.info(f"Destino: [bold cyan]{tg_chat.title}[/] [dim](ID: {tg_chat.id})[/]")

    return UploadContext(
        client=client,
        db=db,
        discovery=discovery,
        tg_chat=tg_chat,
        owner=owner,
        settings=settings,
    )


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

    # --- Scaneo y Informe ---
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

    # --- Subida de lo encontrado ---
    UI.info(f"[dim]Procesando {len(scan_report.found)} archivos[/dim]")
    with state.scope() as (client, db):

        u_ctx = prepare_upload_context(client, db, settings)
        uploader = UploadService(u_ctx)

        for path in scan_report.found:
            job, _ = get_or_create_job(path, u_ctx)

            report = u_ctx.discovery.investigate(job)
            if report.state == AvailabilityState.FULFILLED:
                job.set_uploaded()
            elif report.state == AvailabilityState.NEEDS_UPLOAD:
                uploader.execute_physical_upload(job, path)
            elif report.state == AvailabilityState.CAN_FORWARD:
                uploader.execute_smart_forward(job, report)
            else:
                raise ValueError(f"Invalid state: {report.state}")

            SnapshotService.generate_snapshot(job)
