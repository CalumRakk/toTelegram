from pathlib import Path
from typing import TYPE_CHECKING, List, cast

import peewee
import tartape
import typer

from totelegram.cli.ui import UI, console
from totelegram.discovery import DiscoveryService
from totelegram.identity import Settings
from totelegram.models import Job, Source, TelegramChat, TelegramUser
from totelegram.schemas import (
    ScanReport,
)
from totelegram.types import UploadContext
from totelegram.utils import delete_snapshot, has_snapshot, is_excluded

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Chat, User


def get_or_create_tape(
    path: Path,
    u_ctx: UploadContext,
    force: bool,
) -> Source:
    if tartape.exists(path) and not force:
        try:
            tape = tartape.Tape(path)
            with UI.loading("Verificando integridad de cinta..."):
                tape.verify(raise_exception=True)
            return Source.get_or_create_from_tape(tape)

        except (peewee.DoesNotExist, Exception):
            # Si no está en DB o la cinta está corrupta,
            # caemos en la creación/regeneración de abajo
            pass

    exclusion_patterns = u_ctx.settings.all_exclusion_patterns()
    with UI.loading("Generando índice de cinta..."):
        tape = tartape.create(
            path,
            exclude=exclusion_patterns,
            calculate_hashes=True,
            overwrite=force,
        )
        return Source.create_from_tape(tape, exclusion_patterns)


def get_or_create_job(
    path: Path,
    u_ctx: UploadContext,
    force: bool,
) -> Job:
    chat_db, _ = TelegramChat.get_or_create_from_chat(u_ctx.tg_chat)

    if path.is_dir():
        source = get_or_create_tape(path, u_ctx, force)
    else:
        with console.status(f"[dim]Procesando {path}...[/dim]"):
            source = Source.get_or_create_from_filepath(path)

    job = Job.get_for_source_in_chat(source, chat_db)
    if job and not force:
        UI.info(f"Ya existe un contrato de disponibilidad para [bold]{path.name}[/]")
        return job

    if job and force:
        UI.info(f"Invalidando contrato previo para [bold]{path.name}[/]")
        delete_snapshot(path)
        job.mark_deleted()
        job = None

    tg_limit = (
        u_ctx.settings.tg_max_size_premium
        if u_ctx.owner.is_premium
        else u_ctx.settings.tg_max_size_normal
    )
    job = Job.formalize_intent(source, chat_db, u_ctx.owner.is_premium, tg_limit)
    UI.success("Nuevo contrato de subida generado.")
    return job


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

    return UploadContext(
        client=client,
        db=db,
        discovery=discovery,
        tg_chat=tg_chat,
        owner=owner,
        settings=settings,
    )


class InventoryEngine:
    def __init__(self, settings: Settings, force: bool = False):
        self.settings = settings
        self.patterns = settings.all_exclusion_patterns()
        self.max_size = settings.max_filesize_bytes
        self.force = force

    def _validate_file(
        self, path: Path, report: ScanReport, check_snapshot: bool
    ) -> bool:
        """
        Comprueba: Patrones, Tamaño y (opcionalmente) Snapshot.
        """
        if path.suffix == ".xz" and path.name.endswith(".json.xz"):
            return False

        if check_snapshot and has_snapshot(path):
            if not self.force:
                report.log_skip(path, "snapshot")
                return False

        if is_excluded(path, self.patterns):
            report.log_skip(path, "exclusion")
            return False

        if path.stat().st_size > self.max_size:
            report.log_skip(path, "size")
            return False

        return True

    def _validate_container(self, path: Path, report: ScanReport) -> bool:
        """
        Comprueba Patrones, Snapshot y integridad de cinta de la carpeta. No comprueba tamaño.
        """
        # Si la carpeta está en la lista de exclusión (ej: node_modules), se salta entera.
        if is_excluded(path, self.patterns):
            report.log_skip(path, "exclusion")
            return False

        # Si la carpeta ya fue archivada como tal.
        if has_snapshot(path):
            if not self.force:
                report.log_skip(path, "snapshot")
                return False

        if next(path.iterdir(), None) is None:
            report.log_skip(path, "empty")
            return False

        tape = tartape.get_tape(path)
        if tape is not None and not self.force:
            if not tape.verify(deep=False):
                report.log_skip(path, "integrity")
                return False

        return True

    def scan_granular(self, paths: List[Path]) -> ScanReport:
        """Filtra archivos. Si recibe una carpeta la explora recursivamente."""
        report = ScanReport(exclusion_patterns=self.patterns)
        for p in paths:
            if p.is_file():
                if self._validate_file(p, report, check_snapshot=True):
                    report.found.append(p)
            elif p.is_dir():
                for sub_p in p.rglob("*"):
                    if sub_p.is_file():
                        if self._validate_file(sub_p, report, check_snapshot=True):
                            report.found.append(sub_p)
        return report

    def scan_backup_inventory(self, paths: List[Path]) -> ScanReport:
        """Filtra carpetas para la cinta."""
        report = ScanReport()
        for p in paths:
            if p.is_dir():
                if self._validate_container(p, report):
                    report.found.append(p)
        return report

    def scan_backup_internal(self, folder: Path) -> ScanReport:
        """Filtra el contenido de una carpeta para la cinta."""
        report = ScanReport(exclusion_patterns=self.patterns)
        for p in folder.rglob("*"):
            if p.is_file():
                if self._validate_file(p, report, check_snapshot=False):
                    report.found.append(p)
        return report
