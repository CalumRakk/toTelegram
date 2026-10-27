import logging
from pathlib import Path
from typing import TYPE_CHECKING, List, cast

import peewee
import tartape
import typer

from totelegram.cli.observer import RichUploadObserver
from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI
from totelegram.concurrency import ConcurrencyCoordinator
from totelegram.discovery import DiscoveryService
from totelegram.identity import Settings
from totelegram.models import TelegramUser
from totelegram.schemas import VALUE_NOT_SET, ScanReport
from totelegram.telegram.resolver import TargetResolver
from totelegram.telegram.types import TargetResolutionStatus
from totelegram.types import UploadContext
from totelegram.utils import has_snapshot, is_excluded

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Chat, User

logger = logging.getLogger(__name__)


def prepare_upload_context(
    state: CLIState,
    client: "Client",
    db: peewee.Database,
    settings: Settings,
    target_override: str | None = None,
) -> UploadContext:
    """Prepara el contexto unificado de subida aplicando la jerarquía de resolución de destino."""
    resolver = TargetResolver(client, db)
    resolved_chat_id: int | str | None = None

    # El usuario especificó --target dinámicamente en el comando
    if target_override:
        with UI.loading(f"Resolviendo destino '[bold]{target_override}[/]'..."):
            resolution = resolver.resolve(target_override)

        if resolution.status == TargetResolutionStatus.RESOLVED:
            assert resolution.chat_id is not None
            resolved_chat_id = resolution.chat_id
            logger.info(
                f"Destino temporal resuelto: {resolution.title} ({resolved_chat_id})"
            )

        elif resolution.status == TargetResolutionStatus.AMBIGUOUS:
            UI.error(
                f"El destino '[bold]{target_override}[/]' es ambiguo. "
                f"Se encontraron {len(resolution.candidates)} coincidencias:"
            )
            for c in resolution.candidates:
                identifier = f"@{c.username}" if c.username else str(c.id)
                UI.print(
                    f"  • [bold]{c.title}[/] [dim]({identifier})[/] -> Usa: [yellow]--target {c.id}[/]"
                )
            raise typer.Exit(1)

        else:
            UI.error(
                resolution.error_message
                or f"No se pudo resolver el destino '{target_override}'."
            )
            if resolution.hint:
                UI.tip(resolution.hint)
            raise typer.Exit(1)

    # Fallback al chat por defecto configurado en el perfil
    elif settings.chat_id != VALUE_NOT_SET:
        resolved_chat_id = settings.chat_id

    # Ningún destino disponible (bloqueo instructivo)
    else:
        UI.error("No se ha especificado un chat de destino.")
        UI.educational_tip(
            title="Destino requerido",
            message=(
                "El perfil activo no tiene un chat por defecto y no indicaste '--target'.\n\n"
                "Tienes dos formas de continuar:\n"
                "1. Definir el destino solo para esta subida usando [bold]--target <chat>[/].\n"
                "2. Guardar un destino por defecto permanente usando [bold]totelegram target <chat>[/]."
            ),
            commands=[
                'totelegram send ... --target "Mi Canal"',
                "totelegram send ... --target me",
                'totelegram target "Mi Canal"',
            ],
            spacing="block",
            border_style="yellow",
        )
        raise typer.Exit(1)

    # Obtención del objeto Chat de Pyrogram y usuario activo
    with UI.loading("Sincronizando con Telegram..."):
        try:
            tg_chat = cast("Chat", client.get_chat(resolved_chat_id))
            me = cast("User", client.get_me())
            owner = TelegramUser.get_or_create_from_tg(me)

            # Auto-healing de telegram_account_id para locks distribuidos
            if settings.telegram_account_id is None:
                profile_name = cast(
                    str, state.manager.resolve_profile_name(state.profile_name)
                )
                state.manager.set_setting(profile_name, "telegram_account_id", owner.id)
                settings.telegram_account_id = owner.id
                logger.info(
                    f"Auto-healed: telegram_account_id ({owner.id}) en perfil '{profile_name}'."
                )

        except Exception as e:
            UI.error(f"Error de conexión con el chat destino ({resolved_chat_id}): {e}")
            raise typer.Exit(1)

    discovery = DiscoveryService(client, db)
    coordinator = ConcurrencyCoordinator(
        db=db,
        node_id=state.manager.node_id,
        client=client,
    )
    observer = RichUploadObserver()

    return UploadContext(
        client=client,
        db=db,
        discovery=discovery,
        tg_chat=tg_chat,
        owner=owner,
        settings=settings,
        state=state,
        coordinator=coordinator,
        observer=observer,
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
        # Ignorar snapshots de toTelegram y catalogos sidecar de TarTape
        if path.suffix == ".xz" and path.name.endswith(".json.xz"):
            return False

        if path.suffix == ".tartape" or path.name.endswith(".tartape"):
            return False

        if path.suffix == ".ttape" or path.name.endswith(".ttape"):
            return False

        if check_snapshot and has_snapshot(path) and not self.force:
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
        if has_snapshot(path) and not self.force:
            report.log_skip(path, "snapshot")
            return False

        if next(path.iterdir(), None) is None:
            report.log_skip(path, "empty")
            return False

        tape = tartape.get_tape(path)
        if tape is not None and not self.force and not tape.verify(deep=False):
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
                    if sub_p.is_file() and self._validate_file(
                        sub_p, report, check_snapshot=True
                    ):
                        report.found.append(sub_p)
        return report

    def scan_backup_inventory(self, paths: List[Path]) -> ScanReport:
        """Filtra carpetas para la cinta."""
        report = ScanReport()
        for p in paths:
            if p.is_dir() and self._validate_container(p, report):
                report.found.append(p)
        return report

    def scan_backup_internal(self, folder: Path) -> ScanReport:
        """Filtra el contenido de una carpeta para la cinta."""
        report = ScanReport(exclusion_patterns=self.patterns)
        for p in folder.rglob("*"):
            if p.is_file() and self._validate_file(p, report, check_snapshot=False):
                report.found.append(p)
        return report
