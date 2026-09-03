import logging
from pathlib import Path
from typing import TYPE_CHECKING, List, cast

import peewee
import tartape
import typer

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI
from totelegram.concurrency import ConcurrencyCoordinator
from totelegram.discovery import DiscoveryService
from totelegram.identity import Settings
from totelegram.models import TelegramUser
from totelegram.schemas import ScanReport
from totelegram.types import UploadContext
from totelegram.utils import has_snapshot, is_excluded

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Chat, User

logger = logging.getLogger(__name__)


def prepare_upload_context(
    state: CLIState, client: "Client", db: peewee.Database, settings: Settings
) -> UploadContext:
    with UI.loading("Sincronizando con Telegram..."):
        try:
            tg_chat = cast("Chat", client.get_chat(settings.chat_id))
            me = cast("User", client.get_me())
            owner = TelegramUser.get_or_create_from_tg(me)

            if settings.telegram_account_id is None:
                profile_name = cast(
                    str, state.manager.resolve_profile_name(state.profile_name)
                )
                state.manager.set_setting(profile_name, "telegram_account_id", owner.id)
                settings.telegram_account_id = owner.id
                logger.info(
                    f"Auto-healed: telegram_account_id ({owner.id}) agregado al perfil '{profile_name}'."
                )

        except Exception as e:
            UI.error(f"Error de conexión: {e}")
            raise typer.Exit(1)

    discovery = DiscoveryService(client, db)
    coordinator = ConcurrencyCoordinator(db, node_id=state.manager.node_id)

    return UploadContext(
        client=client,
        db=db,
        discovery=discovery,
        tg_chat=tg_chat,
        owner=owner,
        settings=settings,
        state=state,
        coordinator=coordinator,
    )


class InventoryEngine:
    def __init__(self, settings: Settings, force: bool = False):
        self.settings = settings
        self.patterns = settings.exclude_files
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
