import logging
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

import peewee
import tartape
import typer

from totelegram.cli.observer import RichUploadObserver
from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, console
from totelegram.cli.views.upload import DisplayIndexer, DisplayUpload
from totelegram.concurrency import ConcurrencyCoordinator
from totelegram.database import DatabaseSession
from totelegram.discovery import DiscoveryService
from totelegram.engine.pipeline import JobPipeline
from totelegram.exceptions import (
    ConfigurationError,
    IncompatibleCapacityError,
    InfrastructureError,
    TargetAmbiguousError,
    TargetNotSetError,
)
from totelegram.identity import Settings
from totelegram.models import Source, TelegramUser
from totelegram.packaging.indexer import TapeIndexService
from totelegram.schemas import ScanReport, SourceType
from totelegram.telegram.patches import get_patch_status
from totelegram.telegram.resolver import TargetResolver
from totelegram.telegram.types import TargetResolutionStatus
from totelegram.types import (
    JobExecutionResult,
    UploadSession,
)
from totelegram.utils import has_snapshot, is_excluded

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Chat, User

logger = logging.getLogger(__name__)


class UploadRequest:
    """Encapsula la intención de carga del usuario, sus políticas y confirmaciones."""

    def __init__(
        self,
        state: CLIState,
        paths: list[Path],
        target: str | None = None,
        force: bool = False,
        yes: bool = False,
        is_backup: bool = False,
        auto_truncate: bool = False,
    ):
        self.state = state
        self.paths = paths
        self.target = target
        self.force = force
        self.yes = yes
        self.is_backup = is_backup
        self.auto_truncate = auto_truncate

    @classmethod
    def from_cli(
        cls,
        ctx: typer.Context,
        paths: list[Path],
        target: str | None = None,
        force: bool = False,
        yes: bool = False,
        is_backup: bool = False,
        auto_truncate: bool | None = None,
    ) -> "UploadRequest":
        """Factoría que normaliza las opciones de CLI contra la configuración activa."""
        state: CLIState = ctx.obj
        settings = state.settings

        final_auto_truncate = (
            auto_truncate if auto_truncate is not None else settings.auto_truncate
        )

        return cls(
            state=state,
            paths=paths,
            target=target,
            force=force,
            yes=yes,
            is_backup=is_backup,
            auto_truncate=final_auto_truncate,
        )

    @property
    def item_label(self) -> str:
        """Etiqueta en singular según la naturaleza del recurso."""
        return "carpeta" if self.is_backup else "archivo"

    @property
    def item_plural_label(self) -> str:
        """Etiqueta en plural según la naturaleza del recurso."""
        return "carpetas" if self.is_backup else "archivos"

    def confirm_cleanup(self, count: int, filename: str) -> bool:
        """Gestiona la confirmación interactiva para eliminar partes previas ante un --force."""
        if self.yes:
            return True

        console.print()
        UI.warn(
            f"Existen [bold]{count}[/] partes previas en Telegram del archivo '[bold]{filename}[/]'.\n"
            f"Al continuar con [bold]--force[/], se eliminarán de Telegram para reiniciar la subida desde cero."
        )
        return typer.confirm("¿Deseas eliminarlas y continuar?", default=False)

    @contextmanager
    def open_database(self) -> Generator[peewee.Database, None, None]:
        """Abre la base de datos local SQLite para toda la duración del comando."""
        db_target = getattr(self.state.manager, "database_path", None) or ":memory:"
        with DatabaseSession(db_target, auto_init_schema=True) as db:
            yield db

    @contextmanager
    def open_preflight_session(
        self, db: peewee.Database
    ) -> Generator[UploadSession, None, None]:
        """Abre una conexión inicial a Telegram para validar destino, identidad y escanear inventario."""
        self.state.announce_profile()

        if self.force:
            UI.warn(
                f"Forzando la subida de {self.item_plural_label} sin comprobar el estado previo."
            )

        with self.state.scope() as (client, _):
            session = prepare_upload_session(
                state=self.state,
                client=client,
                db=db,
                settings=self.state.settings,
                target_override=self.target,
            )
            yield session

    # Alias para retrocompatibilidad
    open_session = open_preflight_session


class CandidateItem:
    """Representa un archivo o carpeta individual dentro del lote a procesar."""

    def __init__(
        self,
        path: Path,
        index: int,
        total: int,
        is_backup: bool,
        inventory_engine: "InventoryEngine",
    ):
        self.path = path
        self.index = index
        self.total = total
        self.is_backup = is_backup
        self.inventory_engine = inventory_engine

    @property
    def is_last(self) -> bool:
        """Determina si es el último elemento del lote para el control de pausas."""
        return self.index == self.total

    @property
    def label(self) -> str:
        return "Carpeta" if self.is_backup else "Archivo"

    def announce_incompatible_capacity(self, err: IncompatibleCapacityError) -> None:
        """Informa que el elemento fue omitido por requerir una cuenta con mayor capacidad."""
        DisplayUpload.show_incompatible_capacity_panel(err, filename=self.path.name)

    def announce_start(self) -> None:
        """Emite el encabezado y análisis visual previo al inicio de la subida."""
        if self.is_backup:
            DisplayUpload.show_backup_header(self.path.name, self.index, self.total)
            with UI.loading("Analizando contenido..."):
                report_internal = self.inventory_engine.scan_backup_internal(self.path)
                time.sleep(0.3)
            DisplayUpload.show_internal_scan_result(report_internal)
        else:
            UI.separator()

    def announce_already_fulfilled(self) -> None:
        """Informa que el recurso ya se encuentra al 100% en el canal."""
        UI.info(
            f"{self.label} [bold]{self.path.name}[/] ya está 100% completado en Telegram."
        )

    def announce_success(self, result: JobExecutionResult) -> None:
        """Emite el reporte de cierre del elemento según el resultado obtenido."""
        if result.is_completed:
            UI.success(
                f"{self.label} [bold]{self.path.name}[/] procesado exitosamente."
            )
        else:
            UI.info(
                f"Procesamiento parcial de [bold]{self.path.name}[/]: {result.message}"
            )


class UploadInventory:
    """Gestiona el inventario de elementos escaneados, filtros y estado de cintas."""

    def __init__(
        self,
        scan_report: ScanReport,
        request: UploadRequest,
        inventory_engine: "InventoryEngine",
        tg_chat_title: str,
    ):
        self.scan_report = scan_report
        self.request = request
        self.engine = inventory_engine
        self.tg_chat_title = tg_chat_title

    @classmethod
    def discover(
        cls, session: UploadSession, request: UploadRequest
    ) -> "UploadInventory":
        """Ejecuta el escaneo del inventario y realiza comprobaciones previas."""
        engine = InventoryEngine(
            settings=session.settings,
            force=request.force,
            target_chat_id=session.tg_chat.id,
        )

        if request.is_backup:
            with console.status("[dim]Escaneando directorios...[/]"):
                scan_report = engine.scan_backup_inventory(request.paths)

            # Auto-healing o aviso del catálogo de cintas en Telegram
            indexer = TapeIndexService(session.client, session.db)
            tape_report = indexer.auto_heal_or_report(session.tg_chat.id)
            if tape_report.auto_healed:
                UI.info(
                    f"Auto-sanadas [bold]{tape_report.healed_count}[/] cintas no indexadas en segundo plano."
                )
            elif tape_report.needs_manual_sync:
                DisplayIndexer.show_unindexed_notice(tape_report.unindexed_count)
        else:
            with console.status(f"[dim]Escaneando {len(request.paths)} rutas...[/dim]"):
                scan_report = engine.scan_granular(request.paths)

        chat_title = (
            session.tg_chat.title or session.tg_chat.username or str(session.tg_chat.id)
        )
        return cls(
            scan_report=scan_report,
            request=request,
            inventory_engine=engine,
            tg_chat_title=chat_title,
        )

    def ensure_candidates(self) -> list[CandidateItem]:
        """Presenta las omisiones y retorna los candidatos listos para subida."""
        DisplayUpload.show_skip_report(
            self.scan_report,
            item_type=cast("archivo | carpeta", self.request.item_label),  # type: ignore  # noqa: F821
            force_verbose=False if self.request.is_backup else None,
        )
        raw_candidates = self.scan_report.ensure_has_candidates(
            self.request.item_label, self.tg_chat_title
        )

        total = len(raw_candidates)
        return [
            CandidateItem(
                path=path,
                index=idx,
                total=total,
                is_backup=self.request.is_backup,
                inventory_engine=self.engine,
            )
            for idx, path in enumerate(raw_candidates, 1)
        ]

    def report_final_integrity(self) -> None:
        """Advierte al final de la ejecución si carpetas sufrieron alteraciones durante la subida."""
        if self.request.is_backup and self.scan_report.skipped_by_integrity:
            UI.separator()
            DisplayUpload.show_integrity_advice(self.scan_report)


def announce_session_connection(session: UploadSession) -> None:
    """Muestra el banner informativo de conexión y estado de cuenta/destino."""
    status = get_patch_status()
    if not status["applied"]:
        UI.error("Core Engine: Pyrogram Runtime Patches [FAILED]")

    user = session.owner.first_name or session.owner.username
    chat_n = session.tg_chat.title or session.tg_chat.username
    UI.success(f"Conectado como [bold]{user}[/]")

    if not session.settings.has_custom_app:
        UI.warn("Cuenta en modo público compartido (my.telegram.org recomendado).")

    UI.info(f"Destino: [bold cyan]{chat_n}[/] [dim](ID: {session.tg_chat.id})[/]")
    UI.print("", indent=False)


def prepare_upload_session(
    state: CLIState,
    client: "Client",
    db: peewee.Database,
    settings: Settings,
    target_override: str | None = None,
) -> UploadSession:
    """Prepara el entorno técnico de subida resolviendo el destino obligatorio."""
    if not target_override:
        raise TargetNotSetError()

    resolver = TargetResolver(client, db)
    resolved_chat_id: int | str | None = None

    with UI.loading(f"Resolviendo destino '[bold]{target_override}[/]'..."):
        resolution = resolver.resolve(target_override)

    if resolution.status == TargetResolutionStatus.RESOLVED:
        assert resolution.chat_id is not None
        resolved_chat_id = resolution.chat_id
        logger.info(f"Destino resuelto: {resolution.title} ({resolved_chat_id})")
    elif resolution.status == TargetResolutionStatus.AMBIGUOUS:
        raise TargetAmbiguousError(target_override, resolution.candidates)
    else:
        raise ConfigurationError(
            message=resolution.error_message
            or f"No se pudo resolver el destino '{target_override}'.",
            hint=resolution.hint,
            exit_code=1,
        )

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

    def _is_file_backed_up(self, path: Path) -> bool:
        """Comprueba si el archivo ya está 100% respaldado en el chat destino."""
        if has_snapshot(path):
            return True

        canonical_path = str(path.resolve())
        stat = path.stat()

        # Búsqueda rápida en DB
        source = Source.get_or_none(Source.path_str == canonical_path)
        if not source:
            # Fallback para archivos movidos
            source = (
                Source.select()
                .where((Source.size == stat.st_size) & (Source.type == SourceType.FILE))
                .first()
            )
            if not (
                source
                and Path(source.path_str).name == path.name
                and Source._is_mtime_matching(source.mtime, stat.st_mtime)
            ):
                return False

        # Comprobar frescura y Job completado en el chat
        if source.size != stat.st_size or not Source._is_mtime_matching(
            source.mtime, stat.st_mtime
        ):
            return False

        if self.target_chat_id is None:
            return any(
                j.is_completed_local and j.has_remote_snapshot for j in source.jobs
            )

        return any(
            j.is_completed_local
            and j.has_remote_snapshot
            and str(j.chat_id) == str(self.target_chat_id)
            for j in source.jobs
        )

    def _is_container_backed_up(self, path: Path) -> bool:
        """Comprueba si la carpeta está respaldada como cinta en el chat destino."""
        if has_snapshot(path):
            return True

        canonical_path = str(path.resolve())
        source = Source.get_or_none(
            (Source.path_str == canonical_path) & (Source.type == SourceType.FOLDER)
        )
        if not source:
            return False

        if self.target_chat_id is None:
            return any(
                j.is_completed_local and j.has_remote_snapshot for j in source.jobs
            )

        return any(
            j.is_completed_local
            and j.has_remote_snapshot
            and str(j.chat_id) == str(self.target_chat_id)
            for j in source.jobs
        )

    def _validate_file(
        self, path: Path, report: ScanReport, check_snapshot: bool
    ) -> bool:
        # Ignorar artefactos de toTelegram
        if path.suffix == ".xz" and path.name.endswith(".json.xz"):
            return False
        if path.suffix in (".tartape", ".ttape") or path.name.endswith(
            (".tartape", ".ttape")
        ):
            return False

        # Validación directa contra DB / Snapshot visual
        if (check_snapshot and not self.force) and self._is_file_backed_up(path):
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

        # Comprobación de carpeta en DB / Snapshot visual
        if not self.force and self._is_container_backed_up(path):
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

    def scan_granular(self, paths: list[Path]) -> ScanReport:
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

    def scan_backup_inventory(self, paths: list[Path]) -> ScanReport:
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
    paths: list[Path],
    target: str | None = None,
    force: bool = False,
    yes: bool = False,
    is_backup: bool = False,
    auto_truncate: bool | None = None,
) -> None:
    """Orquesta el flujo de subida dejando visible el árbol de decisiones de toTelegram."""

    request = UploadRequest.from_cli(
        ctx=ctx,
        paths=paths,
        target=target,
        force=force,
        yes=yes,
        is_backup=is_backup,
        auto_truncate=auto_truncate,
    )

    with request.open_database() as db:
        # breve conexión inicial para resolver destino, usuario e inventario
        with request.open_preflight_session(db) as preflight:
            announce_session_connection(preflight)
            inventory = UploadInventory.discover(session=preflight, request=request)
            candidates = inventory.ensure_candidates()

            resolved_target_chat = preflight.tg_chat
            resolved_owner = preflight.owner
            resolved_settings = preflight.settings

        processed_count = 0
        skipped_by_capacity: list[CandidateItem] = []

        observer = RichUploadObserver()
        pipeline = JobPipeline(
            state=request.state,
            db=db,
            target_chat=resolved_target_chat,
            owner=resolved_owner,
            settings=resolved_settings,
            observer=observer,
        )

        for item in candidates:
            item.announce_start()

            # Planificación Offline (Hash MD5)
            job = pipeline.plan_job(
                path=item.path,
                force=request.force,
                auto_truncate=request.auto_truncate,
            )

            # Sesión de Red en Telegram (Investigar -> Transferir -> Sellar)
            try:
                with pipeline.open_telegram_session(
                    job=job,
                    force=request.force,
                    yes=request.yes,
                    confirm_cleanup=request.confirm_cleanup,
                ) as session:
                    report = session.investigate()

                    if report.is_fulfilled:
                        session.seal()
                        item.announce_already_fulfilled()
                    else:
                        session.transfer()
                        session.seal()

                    execution_result = session.result

                # Anuncio inmediato de resultado
                item.announce_success(execution_result)
                processed_count += 1

                # Pausa de seguridad si aplica.
                pipeline.apply_pause(is_last=item.is_last, result=execution_result)

            except IncompatibleCapacityError as e:
                item.announce_incompatible_capacity(e)
                skipped_by_capacity.append(item)
                continue

        inventory.report_final_integrity()

        if skipped_by_capacity and processed_count == 0:
            raise typer.Exit(1)
