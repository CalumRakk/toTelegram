import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, cast

import peewee
import tartape
import typer

from totelegram.cli.observer import RichUploadObserver
from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, console
from totelegram.cli.views.upload import DisplayIndexer, DisplayUpload
from totelegram.concurrency import ConcurrencyCoordinator
from totelegram.discovery import DiscoveryService
from totelegram.engine.pipeline import JobPipeline
from totelegram.exceptions import (
    ConfigurationError,
    InfrastructureError,
    TargetAmbiguousError,
    TargetNotSetError,
)
from totelegram.identity import Settings
from totelegram.models import TelegramUser
from totelegram.packaging.indexer import TapeIndexService
from totelegram.packaging.ledger import ReceiptLedger
from totelegram.schemas import VALUE_NOT_SET, ScanReport
from totelegram.telegram.patches import get_patch_status
from totelegram.telegram.resolver import TargetResolver
from totelegram.telegram.types import TargetResolutionStatus
from totelegram.types import (
    UploadSession,
)
from totelegram.utils import is_excluded

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Chat, User

logger = logging.getLogger(__name__)


def prepare_upload_session(
    state: CLIState,
    client: "Client",
    db: peewee.Database,
    settings: Settings,
    target_override: str | None = None,
) -> UploadSession:
    """Prepara el entorno técnico de subida aplicando la jerarquía de resolución de destino."""
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
            raise TargetAmbiguousError(target_override, resolution.candidates)

        else:
            raise ConfigurationError(
                message=resolution.error_message
                or f"No se pudo resolver el destino '{target_override}'.",
                hint=resolution.hint,
                exit_code=1,
            )

    # Fallback al chat por defecto configurado en el perfil
    elif settings.chat_id != VALUE_NOT_SET:
        resolved_chat_id = settings.chat_id

    # Ningún destino disponible (bloqueo instructivo)
    else:
        raise TargetNotSetError()

    # Sincronización con Telegram
    with UI.loading("Sincronizando con Telegram..."):
        try:
            tg_chat = cast("Chat", client.get_chat(resolved_chat_id))
            time.sleep(0.5)
            me = cast("User", client.get_me())
            owner = TelegramUser.get_or_create_from_tg(me)

            # Auto-healing de telegram_account_id para locks distribuidos
            if settings.telegram_account_id is None:
                profile_name = state.profile_name
                state.manager.set_setting(profile_name, "telegram_account_id", owner.id)
                settings.telegram_account_id = owner.id
                logger.info(
                    f"Auto-healed: telegram_account_id ({owner.id}) en perfil '{profile_name}'."
                )

        except Exception as e:
            raise InfrastructureError(
                f"Error de conexión con el chat destino ({resolved_chat_id}): {e}",
                title="Error de Conexión",
                exit_code=1,
            ) from e

    discovery = DiscoveryService(client, db)
    coordinator = ConcurrencyCoordinator(
        db=db,
        node_id=state.manager.node_id,
        client=client,
    )

    return UploadSession(
        client=client,
        db=db,
        tg_chat=tg_chat,
        owner=owner,
        settings=settings,
        coordinator=coordinator,
        discovery=discovery,
    )


class InventoryEngine:
    def __init__(
        self,
        settings: Settings,
        force: bool = False,
        target_chat_id: int | str | None = None,
    ):
        self.settings = settings
        self.patterns = settings.exclude_files
        self.max_size = settings.max_filesize_bytes
        self.force = force
        self.target_chat_id = target_chat_id

    def _validate_file(
        self, path: Path, report: ScanReport, check_snapshot: bool
    ) -> bool:
        # Ignorar archivos internos de toTelegram y tartape
        if path.suffix == ".xz" and path.name.endswith(".json.xz"):
            return False
        if path.name == ".totelegram.json" or path.name.startswith(".totelegram.tmp"):
            return False
        if path.suffix in (".tartape", ".ttape") or path.name.endswith(
            (".tartape", ".ttape")
        ):
            return False

        # Validación contra el Ledger por Canal
        if (check_snapshot and not self.force) and ReceiptLedger.is_backed_up(
            path, self.target_chat_id
        ):
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
        if is_excluded(path, self.patterns):
            report.log_skip(path, "exclusion")
            return False

        # Comprobación de carpeta en el Ledger
        if not self.force and ReceiptLedger.is_backed_up(path, self.target_chat_id):
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


def run_upload_flow(
    ctx: typer.Context,
    paths: List[Path],
    target: str | None = None,
    force: bool = False,
    yes: bool = False,
    is_backup: bool = False,
    auto_truncate: bool | None = None,
) -> None:
    state: CLIState = ctx.obj
    state.announce_profile()
    settings = state.settings

    final_auto_truncate = (
        auto_truncate if auto_truncate is not None else settings.auto_truncate
    )

    if force:
        item_word = "carpetas" if is_backup else "archivos"
        UI.warn(f"Forzando la subida de {item_word} sin comprobar el estado previo.")

    with state.scope() as (client, db):
        session = prepare_upload_session(
            state=state,
            client=client,
            db=db,
            settings=settings,
            target_override=target,
        )

        inventory = InventoryEngine(
            settings=settings,
            force=force,
            target_chat_id=session.tg_chat.id,
        )

        # Escaneo de inventario declarativo
        if is_backup:
            with console.status("[dim]Escaneando directorios...[/]"):
                scan_report = inventory.scan_backup_inventory(paths)

            DisplayUpload.show_skip_report(scan_report, "carpeta", force_verbose=False)
            candidates = scan_report.ensure_has_candidates(
                "carpeta", session.tg_chat.title
            )

            # Verificación del catálogo de cintas en Telegram
            indexer = TapeIndexService(client, db)
            tape_report = indexer.auto_heal_or_report(session.tg_chat.id)
            if tape_report.auto_healed:
                UI.info(
                    f"Auto-sanadas [bold]{tape_report.healed_count}[/] cintas no indexadas en segundo plano."
                )
            elif tape_report.needs_manual_sync:
                DisplayIndexer.show_unindexed_notice(tape_report.unindexed_count)
        else:
            with console.status(f"[dim]Escaneando {len(paths)} rutas...[/dim]"):
                scan_report = inventory.scan_granular(paths)
            DisplayUpload.show_skip_report(scan_report, "archivo")
            candidates = scan_report.ensure_has_candidates(
                "archivo", session.tg_chat.title
            )

        # Diagnóstico de runtime patches y banner de sesión
        status = get_patch_status()
        if not status["applied"]:
            UI.error("Core Engine: Pyrogram Runtime Patches [FAILED]")

        user = session.owner.first_name or session.owner.username
        chat_n = session.tg_chat.title or session.tg_chat.username
        UI.success(f"Conectado como [bold]{user}[/]")

        if not settings.has_custom_app:
            UI.warn("Cuenta en modo público compartido (my.telegram.org recomendado).")

        UI.info(f"Destino: [bold cyan]{chat_n}[/] [dim](ID: {session.tg_chat.id})[/]")
        UI.print("", indent=False)

        def confirm_cleanup_handler(count: int, filename: str) -> bool:
            console.print()
            UI.warn(
                f"Existen [bold]{count}[/] partes previas en Telegram del archivo '[bold]{filename}[/]'.\n"
                f"Al continuar con [bold]--force[/], se eliminarán de Telegram para reiniciar la subida desde cero."
            )
            return typer.confirm("¿Deseas eliminarlas y continuar?", default=False)

        # Procesamiento secuencial de candidatos (Sin try/except gigantescos)
        total_candidates = len(candidates)

        with session.exclusive_lock():
            for index, item_path in enumerate(candidates, 1):
                is_last = index == total_candidates

                if is_backup:
                    DisplayUpload.show_backup_header(
                        item_path.name, index, total_candidates
                    )
                    with UI.loading("Analizando contenido..."):
                        report_internal = inventory.scan_backup_internal(item_path)
                        time.sleep(0.3)
                    DisplayUpload.show_internal_scan_result(report_internal)
                else:
                    UI.separator()

                observer = RichUploadObserver()
                pipeline = JobPipeline(session=session, observer=observer)

                # Delegar la ejecución al pipeline unificado
                result = pipeline.process(
                    path=item_path,
                    is_last_in_batch=is_last,
                    force=force,
                    auto_truncate=final_auto_truncate if is_backup else False,
                    yes=yes,
                    confirm_cleanup=confirm_cleanup_handler,
                )

                item_label = "Carpeta" if is_backup else "Archivo"
                if result.is_completed:
                    UI.success(
                        f"{item_label} [bold]{item_path.name}[/] procesado exitosamente."
                    )
                else:
                    UI.info(
                        f"Procesamiento parcial de [bold]{item_path.name}[/]: {result.message}"
                    )

        # Alertas finales para carpetas con cambios de integridad
        if is_backup and scan_report.skipped_by_integrity:
            UI.separator()
            DisplayUpload.show_integrity_advice(scan_report)
