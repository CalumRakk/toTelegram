import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Tuple, cast

import tartape
import typer

from totelegram.cli.commands.config import _get_config_tools, handle_config_errors
from totelegram.cli.ui.console import UI, console
from totelegram.cli.ui.views import DisplayUpload
from totelegram.common.consts import VALUE_NOT_SET, Commands
from totelegram.common.enums import AvailabilityState
from totelegram.common.schemas import CLIState, ScanReport
from totelegram.common.utils import is_excluded
from totelegram.logic.chunker import Chunker
from totelegram.logic.discovery import DiscoveryService
from totelegram.logic.snapshot import SnapshotService
from totelegram.logic.uploader import UploadService
from totelegram.manager.models import (
    Job,
    Payload,
    RemotePayload,
    SourceFile,
    TelegramChat,
    TelegramUser,
)
from totelegram.manager.setting import Settings

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Chat, User


def get_or_create_job(
    path: Path, tg_chat: "Chat", is_premium: bool, settings: Settings
) -> Tuple[Job, bool, SourceFile]:
    chat_db, _ = TelegramChat.get_or_create_from_chat(tg_chat)

    if path.is_dir():
        exclusion_patterns = settings.all_exclusion_patterns()
        if not tartape.exists(path):
            with UI.loading("Generando cinta..."):
                tape = tartape.create(
                    path,
                    exclude=exclusion_patterns,
                    calculate_hashes=True,
                )
                try:
                    source = SourceFile.from_tape(path, tape)
                except Exception as e:
                    tape.destroy()
                    raise
        else:
            tape = tartape.open(path)
            source = SourceFile.get(SourceFile.md5sum == tape.fingerprint)
            if not tape.verify():
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
            source = SourceFile.get_or_create_from_path(path)

    job = Job.get_for_source_in_chat(source, chat_db)
    if not job:
        tg_limit = (
            settings.tg_max_size_premium if is_premium else settings.tg_max_size_normal
        )
        job = Job.formalize_intent(source, chat_db, is_premium, tg_limit)
        UI.info(
            f"Nuevo contrato de disponibilidad creado (Límite: {tg_limit / (1024*1024):.0f}MB)"
        )
        return job, True, source

    return job, False, source


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
        upload_limit_rate_kbps = settings.upload_limit_rate_kbps
        max_filename_length = settings.max_filename_length
        discovery = DiscoveryService(client, db)
        uploader = UploadService(client, upload_limit_rate_kbps, max_filename_length)

        with UI.loading("Sincronizando con Telegram..."):
            tg_chat = cast("Chat", client.get_chat(settings.chat_id))
            me = cast("User", client.get_me())
            owner = TelegramUser.get_or_create_from_tg(me)

        UI.success(f"Conectado como [bold]{me.first_name or me.username}[/]")
        UI.info(f"Destino: [bold cyan]{tg_chat.title}[/] [dim](ID: {tg_chat.id})[/]")

        for path in scan_report.found:
            job, _, source = get_or_create_job(path, tg_chat, me.is_premium, settings)

            report = discovery.investigate(job)
            if report.state == AvailabilityState.FULFILLED:
                UI.success(
                    "¡Operación completada! Todos los volúmenes ya están en el destino."
                )
                job.set_uploaded()
                continue

            elif report.state == AvailabilityState.NEEDS_UPLOAD:
                payloads = Chunker.get_or_create(job)
                Payload.bulk_create(payloads)
                for payload in payloads:
                    if payload.has_remote:
                        continue

                    message = uploader.upload_payload(
                        tg_chat.id, path, payload, source.md5sum
                    )
                    RemotePayload.register_upload(payload, message, owner)

            elif report.state == AvailabilityState.CAN_FORWARD:
                job = report.remotes[0].job  # type: ignore
                payloads = Chunker.get_or_create(job)
                Payload.bulk_create(payloads)
                for payload in payloads:
                    if payload.has_remote:
                        continue
                    remote = next(
                        i
                        for i in report.remotes
                        if i.payload.sequence_index == payload.sequence_index
                    )
                    message = uploader.smart_forward_strategy(
                        tg_chat.id, remote, payload, source.md5sum
                    )
                    RemotePayload.register_upload(payload, message, owner)
                    time.sleep(1)
            else:
                raise ValueError(f"Invalid state: {report.state}")

            job.set_uploaded()
            SnapshotService.generate_snapshot(job)
