import logging
import random
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from totelegram.concurrency import ConcurrencyCoordinator
from totelegram.database import db_transaction
from totelegram.discovery import DiscoveryService
from totelegram.engine.events import NullObserver, UploadObserver
from totelegram.engine.planner import JobPlanner
from totelegram.engine.relocator import ChunkRelocator
from totelegram.engine.uploader import CooperativeUploader
from totelegram.exceptions import (
    ConcurrencyError,
    IncompatibleCapacityError,
    PermissionDeniedCleanupError,
    PipelineCancelledError,
)
from totelegram.identity import Settings
from totelegram.models import Job, Payload, TelegramChat, TelegramUser
from totelegram.packaging.publisher import SnapshotPublisher
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import ExecutionDecision
from totelegram.types import (
    InvestigationReport,
    JobExecutionResult,
    StrategyResult,
    UploadSession,
)

if TYPE_CHECKING:
    import peewee
    from pyrogram.types import Chat

    from totelegram.cli.state import CLIState

logger = logging.getLogger(__name__)


class JobSession:
    """Representa la sesión activa de red en Telegram para procesar un Job específico."""

    def __init__(
        self,
        pipeline: "JobPipeline",
        session: UploadSession,
        job: Job,
        force: bool = False,
        yes: bool = False,
        confirm_cleanup: Callable[[int, str], bool] | None = None,
    ):
        self.pipeline = pipeline
        self.session = session
        self.observer = pipeline.observer
        self.job = job
        self.path = job.path
        self.force = force
        self.yes = yes
        self.confirm_cleanup = confirm_cleanup

        self.account_max_size = (
            self.session.settings.tg_max_size_premium
            if self.session.owner.is_premium
            else self.session.settings.tg_max_size_normal
        )

        self.report: InvestigationReport | None = None
        self.strategy_result: StrategyResult | None = None
        self.is_completed: bool = False
        self.snapshot_generated: bool = False

    @property
    def is_fulfilled(self) -> bool:
        return self.report is not None and self.report.is_fulfilled

    def investigate(self) -> InvestigationReport:
        """Analiza el estado físico JIT en Telegram con conexión activa y fresca."""
        report = self.session.discovery.investigate(
            job=self.job,
            force=self.force,
            account_max_chunk_size=self.account_max_size,
        )

        if report.potential_duplicates:
            self.observer.on_potential_duplicates(report.potential_duplicates)

        logger.info(f"Decisión de ejecución resuelta: {report.decision.value}")
        self.report = report

        if report.decision == ExecutionDecision.ALREADY_FULFILLED:
            self.strategy_result = StrategyResult(
                strategy_name="None",
                pieces_processed=0,
                did_upload_bytes=False,
                is_completed=True,
                message="El archivo ya se encuentra 100% completado en este chat.",
            )
            self.is_completed = True
            return self.report

        if report.decision == ExecutionDecision.PERMISSION_DENIED_CLEANUP:
            raise PermissionDeniedCleanupError(
                chat_id=self.session.tg_chat.id,
                reason=report.detail
                or "Permiso 'can_delete_messages' requerido para limpiar el canal.",
            )

        if report.decision == ExecutionDecision.INCOMPATIBLE_CAPACITY:
            valid_payload_ids = {r.payload_id for r in report.valid_remotes}
            pending_payloads = [
                p
                for p in self.job.payloads.order_by(Payload.sequence_index)
                if p.id not in valid_payload_ids
            ]
            pending_size = (
                pending_payloads[0].size if pending_payloads else self.job.chunk_size
            )

            raise IncompatibleCapacityError(
                job_chunk_size=self.job.chunk_size,
                account_max_size=self.account_max_size,
                is_premium=self.session.owner.is_premium,
                pending_payload_size=pending_size,
            )

        return self.report

    def transfer(self) -> StrategyResult:
        """Prepara el estado del chat y ejecuta la subida física de trozos pendientes."""
        if self.is_fulfilled:
            assert self.strategy_result is not None
            return self.strategy_result

        assert self.report is not None, (
            "Debe ejecutarse investigate() antes de transfer()."
        )

        self.job = self.pipeline._prepare_job_state(
            session=self.session,
            job=self.job,
            path=self.path,
            report=self.report,
            force=self.force,
            account_max_size=self.account_max_size,
            yes=self.yes,
            confirm_cleanup=self.confirm_cleanup,
        )

        self.strategy_result = self.pipeline._execute_transfer(
            self.session, self.job, self.report
        )
        return self.strategy_result

    def seal(self) -> tuple[bool, bool]:
        """Genera el snapshot, lo publica en Telegram y actualiza el estado local."""
        if self.is_fulfilled:
            self.is_completed = True
            self.snapshot_generated = False
            self.observer.on_job_complete(self.job, snapshot_created=False)
            return self.is_completed, self.snapshot_generated

        self.is_completed, self.snapshot_generated = self.pipeline._seal_job_snapshot(
            self.session, self.job
        )
        self.observer.on_job_complete(self.job, self.snapshot_generated)
        return self.is_completed, self.snapshot_generated

    @property
    def result(self) -> JobExecutionResult:
        """Consolida el resultado final del Job."""
        decision = (
            self.report.decision if self.report else ExecutionDecision.CLEAN_RESTART
        )
        msg = self.strategy_result.message if self.strategy_result else ""
        return JobExecutionResult(
            job=self.job,
            path=self.path,
            decision=decision,
            strategy_result=self.strategy_result,
            is_completed=self.is_completed,
            snapshot_generated=self.snapshot_generated,
            message=msg,
        )


# Alias para retrocompatibilidad
JobContext = JobSession


class JobPipeline:
    """Orquestador de ejecución desacoplado para planificar y procesar Jobs."""

    def __init__(
        self,
        state: "CLIState | None" = None,
        db: "peewee.Database | None" = None,
        target_chat: "Chat | TelegramChat | None" = None,
        owner: "TelegramUser | None" = None,
        settings: "Settings | None" = None,
        observer: UploadObserver | None = None,
        session: UploadSession | None = None,
        tg_chat: "Chat | TelegramChat | None" = None,
    ):
        self.observer = observer or NullObserver()
        self.uploader = CooperativeUploader()

        # Soporte para inyección directa en tests unitarios
        self._test_session = session

        self.state = state
        self.db = db or (session.db if session else None)
        self.target_chat = (
            target_chat or tg_chat or (session.tg_chat if session else None)
        )
        self.owner = owner or (session.owner if session else None)
        self.settings = settings or (session.settings if session else None)

    def plan_job(
        self,
        path: Path,
        force: bool = False,
        auto_truncate: bool = False,
    ) -> Job:
        """Planifica el Job de forma 100% offline contra el disco y SQLite."""
        return JobPlanner.plan(
            path=path,
            observer=self.observer,
            force=force,
            auto_truncate=auto_truncate,
            tg_chat=self.target_chat,
            settings=self.settings,
            owner=self.owner,
        )

    @contextmanager
    def open_telegram_session(
        self,
        job: Job,
        force: bool = False,
        yes: bool = False,
        confirm_cleanup: Callable[[int, str], bool] | None = None,
    ) -> Generator[JobSession, None, None]:
        """Abre una sesión de Telegram y bloqueo de cuenta acotados a la ejecución de este Job."""
        total_pieces = job.payloads.count() or job.total_expected_parts
        self.observer.on_job_start(job, total_pieces)

        if self._test_session is not None:
            # Modo test unitario con sesión inyectada
            job_session = JobSession(
                pipeline=self,
                session=self._test_session,
                job=job,
                force=force,
                yes=yes,
                confirm_cleanup=confirm_cleanup,
            )
            with self._test_session.exclusive_lock():
                yield job_session
            return

        # Modo producción
        assert self.state is not None, (
            "Se requiere CLIState para abrir la sesión de Telegram."
        )
        assert self.db is not None, "Se requiere conexión a la base de datos."
        assert self.target_chat is not None, "Se requiere el chat destino resuelto."
        assert self.owner is not None, "Se requiere el usuario autenticado."
        assert self.settings is not None, "Se requieren los ajustes del perfil."

        with self.state.get_telegram_session() as client:
            coordinator = ConcurrencyCoordinator(
                db=self.db,
                node_id=self.state.manager.node_id,
                client=client,
            )
            discovery = DiscoveryService(client, self.db)
            session = UploadSession(
                client=client,
                db=self.db,
                tg_chat=self.target_chat,  # type: ignore
                owner=self.owner,
                settings=self.settings,
                coordinator=coordinator,
                discovery=discovery,
            )

            job_session = JobSession(
                pipeline=self,
                session=session,
                job=job,
                force=force,
                yes=yes,
                confirm_cleanup=confirm_cleanup,
            )

            with session.exclusive_lock():
                yield job_session

    def apply_pause(
        self, is_last: bool = False, result: JobExecutionResult | None = None
    ) -> None:
        """Aplica la pausa de seguridad de forma 100% offline (sin sockets de red activos)."""
        if is_last:
            return

        if (
            result
            and result.strategy_result
            and not result.strategy_result.did_upload_bytes
        ):
            return

        actual_settings = self.settings or (
            self._test_session.settings if self._test_session else None
        )
        if not actual_settings:
            return

        pause_range = actual_settings.upload_pause_range
        if not pause_range or pause_range == [0, 0]:
            return

        minutes = random.randint(min(pause_range), max(pause_range))
        seconds = minutes * 60

        if seconds > 0:
            self.observer.on_pause_start(seconds, "Inter-job")
            remaining = seconds
            while remaining > 0:
                self.observer.on_pause_tick(remaining, seconds)
                time.sleep(1)
                remaining -= 1
            self.observer.on_pause_end()

    def process(
        self,
        path: Path,
        force: bool = False,
        auto_truncate: bool = False,
        is_last_in_batch: bool = True,
        yes: bool = False,
        confirm_cleanup: Callable[[int, str], bool] | None = None,
    ) -> JobExecutionResult:
        """Ejecuta el ciclo de vida completo de un archivo de forma síncrona."""
        job = self.plan_job(path, force=force, auto_truncate=auto_truncate)
        with self.open_telegram_session(
            job, force=force, yes=yes, confirm_cleanup=confirm_cleanup
        ) as session:
            report = session.investigate()
            if not report.is_fulfilled:
                session.transfer()
            session.seal()
            result = session.result

        if not is_last_in_batch:
            self.apply_pause(is_last=is_last_in_batch, result=result)

        return result

    def _ensure_no_active_claims_on_job(
        self, job: Job, session: UploadSession | None = None
    ) -> None:
        from totelegram.common.helpers import get_utc_now
        from totelegram.models import Claim

        payload_ids = [f"payload:{p.id}" for p in job.payloads]
        if not payload_ids:
            return

        active_session = session or self._test_session
        current_node_id = (
            active_session.coordinator.node_id
            if active_session
            else (self.state.manager.node_id if self.state else "")
        )

        now = get_utc_now()
        active_claims = list(
            Claim.select().where(
                (Claim.resource_id.in_(payload_ids))
                & (Claim.node_id != current_node_id)
                & (Claim.expires_at > now)
            )
        )
        if active_claims:
            holder = active_claims[0].node_id
            raise ConcurrencyError(
                f"No se puede reiniciar ni purgar el Job {job.id} con --force: "
                f"el nodo '{holder}' está procesando activamente una de sus piezas.",
                title="Recurso en uso activo",
                hint="Espera a que la otra subida finalice o que expire su lease.",
            )

    def _prepare_job_state(
        self,
        session: UploadSession,
        job: Job,
        path: Path,
        report: InvestigationReport,
        force: bool,
        account_max_size: int,
        yes: bool,
        confirm_cleanup: Callable[[int, str], bool] | None,
    ) -> Job:
        if report.decision == ExecutionDecision.RELOCATED_AND_RESUMED:
            logger.info(
                f"Job {job.id}: Reubicando {len(report.valid_remotes)} trozos al fondo del chat..."
            )
            self.observer.on_relocation_start(len(report.valid_remotes))
            ChunkRelocator.relocate(
                client=session.client,
                db=session.db,
                chat_id=session.tg_chat.id,
                remotes=report.valid_remotes,
            )
            return job

        if (
            report.decision == ExecutionDecision.CLEAN_RESTART
            and force
            and job.payloads.count() > 0
        ):
            self._ensure_no_active_claims_on_job(job, session=session)
            old_msg_ids = [r.message_id for r in report.valid_remotes]

            if old_msg_ids and not yes and confirm_cleanup:
                confirmed = confirm_cleanup(len(old_msg_ids), path.name)
                if not confirmed:
                    raise PipelineCancelledError(
                        f"Operación cancelada por el usuario para {path.name}."
                    )

            logger.info(f"Job {job.id}: Purgando intento previo con --force...")
            if old_msg_ids:
                try:
                    session.client.delete_messages(
                        chat_id=session.tg_chat.id, message_ids=old_msg_ids
                    )
                except Exception as e:
                    logger.warning(f"Aviso limpiando mensajes en Telegram: {e}")

            target_chunk_size = account_max_size or job.chunk_size
            with db_transaction(session.db):
                source = job.source
                chat = job.chat
                job.purge(session.db)
                job = Job.formalize_intent(source, chat, chunk_size=target_chunk_size)

        return job

    def _execute_transfer(
        self,
        session: UploadSession,
        job: Job,
        report: InvestigationReport,
    ) -> StrategyResult:
        if report.decision == ExecutionDecision.SEAL_HEALED:
            logger.info(f"Job {job.id}: Sellando snapshot faltante (SEAL_HEALED)...")
            return StrategyResult(
                strategy_name="SealHealing",
                pieces_processed=len(report.valid_remotes),
                did_upload_bytes=False,
                is_completed=True,
                message="Todas las partes ya estaban en Telegram. Snapshot sellado exitosamente.",
            )

        return self.uploader.upload(job, session, self.observer)

    def _seal_job_snapshot(self, session: UploadSession, job: Job) -> tuple[bool, bool]:
        if not job.is_completed_local:
            return False, False

        if job.remote_snapshot is not None:
            return True, False

        with tempfile.TemporaryDirectory() as staging_dir:
            staging_path = Path(staging_dir)
            manifest, temp_snapshot_file = SnapshotService.generate_snapshot(
                job, staging_dir=staging_path
            )

            sent_msg = SnapshotPublisher.publish(
                client=session.client,
                chat_id=session.tg_chat.id,
                snapshot_path=temp_snapshot_file,
                manifest=manifest,
            )

            with db_transaction(session.db):
                job.set_snapshot_published(sent_msg.id, session.owner)

        return True, True
