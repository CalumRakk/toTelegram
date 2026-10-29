import logging
import random
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from totelegram.database import db_transaction
from totelegram.engine.events import NullObserver, UploadObserver
from totelegram.engine.planner import JobPlanner
from totelegram.engine.relocator import ChunkRelocator
from totelegram.engine.uploader import CooperativeUploader
from totelegram.exceptions import (
    IncompatibleCapacityError,
    PermissionDeniedCleanupError,
    PipelineCancelledError,
)
from totelegram.models import Job
from totelegram.packaging.ledger import ReceiptLedger
from totelegram.packaging.publisher import SnapshotPublisher
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import ExecutionDecision
from totelegram.types import (
    InvestigationReport,
    JobExecutionResult,
    StrategyResult,
    UploadSession,
)

logger = logging.getLogger(__name__)


class JobContext:
    """Representa el contexto y ciclo de vida de ejecución de un Job individual."""

    def __init__(
        self,
        pipeline: "JobPipeline",
        job: Job,
        path: Path,
        force: bool = False,
        yes: bool = False,
        confirm_cleanup: Callable[[int, str], bool] | None = None,
    ):
        self.pipeline = pipeline
        self.session = pipeline.session
        self.observer = pipeline.observer
        self.job = job
        self.path = path
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
        """Analiza el estado físico JIT en Telegram y evalúa guardias tempranas."""
        report = self.session.discovery.investigate(
            job=self.job,
            force=self.force,
            account_max_chunk_size=self.account_max_size,
        )

        if report.potential_duplicates:
            self.observer.on_potential_duplicates(report.potential_duplicates)

        logger.info(f"Decisión de ejecución resuelta: {report.decision.value}")
        self.report = report

        # Guardias tempranas
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
            raise IncompatibleCapacityError(
                job_chunk_size=self.job.chunk_size,
                account_max_size=self.account_max_size,
                is_premium=self.session.owner.is_premium,
            )

        return self.report

    def transfer(self) -> StrategyResult:
        """Prepara el estado del chat (reubicación/purga) y ejecuta la subida física."""
        if self.is_fulfilled:
            assert self.strategy_result is not None
            return self.strategy_result

        assert self.report is not None, (
            "Debe ejecutarse investigate() antes de transfer()."
        )

        # Preparación del estado del chat
        self.job = self.pipeline._prepare_job_state(
            job=self.job,
            path=self.path,
            report=self.report,
            force=self.force,
            account_max_size=self.account_max_size,
            yes=self.yes,
            confirm_cleanup=self.confirm_cleanup,
        )

        # Ejecución física o sellado directo
        self.strategy_result = self.pipeline._execute_transfer(self.job, self.report)
        return self.strategy_result

    def seal(self) -> tuple[bool, bool]:
        """Genera el snapshot, lo publica en Telegram y actualiza el Ledger."""
        # Si ya estaba 100% completado en Telegram, no re-generamos snapshot
        if self.is_fulfilled:
            self.is_completed = True
            self.snapshot_generated = False
            self.observer.on_job_complete(self.job, snapshot_created=False)
            return self.is_completed, self.snapshot_generated

        self.is_completed, self.snapshot_generated = self.pipeline._seal_job_snapshot(
            self.job
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


class JobPipeline:
    def __init__(
        self,
        session: UploadSession,
        observer: UploadObserver | None = None,
    ):
        self.session = session
        self.observer = observer or NullObserver()
        self.uploader = CooperativeUploader()

    @contextmanager
    def track_job(
        self,
        path: Path,
        is_last_in_batch: bool = False,
        force: bool = False,
        auto_truncate: bool = False,
        yes: bool = False,
        confirm_cleanup: Callable[[int, str], bool] | None = None,
    ) -> Generator[JobContext, None, None]:
        """
        Administrador de contexto declarativo para el ciclo de vida de un Job.
        Planifica la intención, asegura el lock y gestiona la pausa inter-job al finalizar.
        """
        logger.info(f"=== Iniciando Pipeline para: {path.name} ===")

        # Planificación de la intención
        job = JobPlanner.plan(
            path=path,
            session=self.session,
            observer=self.observer,
            force=force,
            auto_truncate=auto_truncate,
        )

        total_pieces = job.payloads.count() or job.total_expected_parts
        self.observer.on_job_start(job, total_pieces)

        context = JobContext(
            pipeline=self,
            job=job,
            path=path,
            force=force,
            yes=yes,
            confirm_cleanup=confirm_cleanup,
        )

        with self.session.exclusive_lock():
            try:
                yield context
            finally:
                # Pausa inter-job si se transmitieron bytes reales y no es el último
                if (
                    not is_last_in_batch
                    and context.strategy_result
                    and context.strategy_result.did_upload_bytes
                ):
                    self._apply_inter_job_pause()

    def process(
        self,
        path: Path,
        is_last_in_batch: bool = False,
        force: bool = False,
        auto_truncate: bool = False,
        yes: bool = False,
        confirm_cleanup: Callable[[int, str], bool] | None = None,
    ) -> JobExecutionResult:
        """Método de fachada retrocompatible: orquesta las 3 fases secuencialmente."""
        with self.track_job(
            path=path,
            is_last_in_batch=is_last_in_batch,
            force=force,
            auto_truncate=auto_truncate,
            yes=yes,
            confirm_cleanup=confirm_cleanup,
        ) as job_ctx:
            report = job_ctx.investigate()
            if not report.is_fulfilled:
                job_ctx.transfer()
            job_ctx.seal()
            return job_ctx.result

    # SUBMÉTODOS PRIVADOS DEL MOTOR (Reubicación, Transferencia, Sellado)

    def _prepare_job_state(
        self,
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
                f"Job {job.id}: Ejecutando reubicación atómica de {len(report.valid_remotes)} trozos..."
            )
            self.observer.on_relocation_start(len(report.valid_remotes))
            ChunkRelocator.relocate(
                client=self.session.client,
                db=self.session.db,
                chat_id=self.session.tg_chat.id,
                remotes=report.valid_remotes,
            )
            return job

        if (
            report.decision == ExecutionDecision.CLEAN_RESTART
            and force
            and job.payloads.count() > 0
        ):
            old_msg_ids = [r.message_id for r in report.valid_remotes]

            if old_msg_ids and not yes and confirm_cleanup:
                confirmed = confirm_cleanup(len(old_msg_ids), path.name)
                if not confirmed:
                    raise PipelineCancelledError(
                        f"Operación cancelada por el usuario para {path.name}."
                    )

            logger.info(
                f"Job {job.id}: Purgando intento previo para reinicio limpio con --force..."
            )
            if old_msg_ids:
                try:
                    self.session.client.delete_messages(
                        chat_id=self.session.tg_chat.id, message_ids=old_msg_ids
                    )
                except Exception as e:
                    logger.warning(f"Aviso limpiando mensajes viejos en Telegram: {e}")

            with db_transaction(self.session.db):
                source = job.source
                chat = job.chat
                job.purge(self.session.db)
                job = Job.formalize_intent(source, chat, chunk_size=account_max_size)

        return job

    def _execute_transfer(
        self,
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

        return self.uploader.upload(job, self.session, self.observer)

    def _seal_job_snapshot(self, job: Job) -> tuple[bool, bool]:
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
                client=self.session.client,
                chat_id=self.session.tg_chat.id,
                snapshot_path=temp_snapshot_file,
                manifest=manifest,
            )

            with db_transaction(self.session.db):
                job.set_snapshot_published(sent_msg.id, self.session.owner)

            ReceiptLedger.record_upload(
                file_path=job.path,
                chat_id=self.session.tg_chat.id,
                snapshot_message_id=sent_msg.id,
                chunk_size=job.chunk_size,
                total_parts=job.payloads.count(),
                md5=job.source.md5sum,
                chat_title=self.session.tg_chat.title,
            )

        return True, True

    def _apply_inter_job_pause(self) -> None:
        pause_range = self.session.settings.upload_pause_range
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
