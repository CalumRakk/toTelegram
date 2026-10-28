import logging
import random
import tempfile
import time
from pathlib import Path

from totelegram.database import db_transaction
from totelegram.engine.planner import JobPlanner
from totelegram.engine.relocator import ChunkRelocator
from totelegram.engine.uploader import CooperativeUploader
from totelegram.models import Job
from totelegram.packaging.ledger import ReceiptLedger
from totelegram.packaging.publisher import SnapshotPublisher
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import ExecutionDecision
from totelegram.types import (
    IncompatibleCapacityError,
    InvestigationReport,
    JobExecutionResult,
    PermissionDeniedCleanupError,
    PipelineCancelledError,
    StrategyResult,
    UploadContext,
)

logger = logging.getLogger(__name__)


class JobPipeline:
    """Orquestador de alto nivel para el procesamiento de archivos y carpetas.

    Ejecuta el ciclo de vida de forma estrictamente declarativa y lineal.
    """

    def __init__(self, ctx: UploadContext):
        self.ctx = ctx
        self.uploader = CooperativeUploader()

    def process(
        self,
        path: Path,
        is_last_in_batch: bool = False,
        force: bool = False,
        auto_truncate: bool = False,
    ) -> JobExecutionResult:
        logger.info(f"=== Iniciando Pipeline para: {path.name} ===")

        # 1. Planificación de la intención
        job = JobPlanner.plan(
            path=path,
            ctx=self.ctx,
            force=force,
            auto_truncate=auto_truncate,
        )

        total_pieces = job.payloads.count() or job.total_expected_parts
        self.ctx.observer.on_job_start(job, total_pieces)

        account_max_size = (
            self.ctx.settings.tg_max_size_premium
            if self.ctx.owner.is_premium
            else self.ctx.settings.tg_max_size_normal
        )

        with self.ctx.coordinator.guard_account(self.ctx.account_id):
            # 2. Investigación física JIT en el chat destino
            report = self.ctx.discovery.investigate(
                job=job,
                force=force,
                account_max_chunk_size=account_max_size,
            )

            if report.potential_duplicates:
                self.ctx.observer.on_potential_duplicates(report.potential_duplicates)

            logger.info(f"Decisión de ejecución resuelta: {report.decision.value}")

            # 3. Guardias tempranas (Fail-Fast o resolución inmediata)
            early_result = self._check_early_termination(
                job, path, report, account_max_size
            )
            if early_result is not None:
                return early_result

            # 4. Preparación del estado del chat (Efectos secundarios: Reubicación o Purga)
            job = self._prepare_job_state(job, path, report, force, account_max_size)

            # 5. Ejecución de la transferencia (o sellado directo si ya estaba completo)
            strategy_result = self._execute_transfer(job, report)

            # 6. Sellado de Snapshot y registro en Ledger si el Job quedó completado
            is_completed, snapshot_generated = self._seal_job_snapshot(job)
            self.ctx.observer.on_job_complete(job, snapshot_generated)

        # 7. Pausa inter-job si se transmitieron bytes reales
        if not is_last_in_batch and strategy_result.did_upload_bytes:
            self._apply_inter_job_pause()

        return JobExecutionResult(
            job=job,
            path=path,
            decision=report.decision,
            strategy_result=strategy_result,
            is_completed=is_completed,
            snapshot_generated=snapshot_generated,
            message=strategy_result.message,
        )

    # -------------------------------------------------------------------------
    # SUBMÉTODOS PRIVADOS DEL PIPELINE
    # -------------------------------------------------------------------------

    def _check_early_termination(
        self,
        job: Job,
        path: Path,
        report: InvestigationReport,
        account_max_size: int,
    ) -> JobExecutionResult | None:
        """Evalúa condiciones terminales inmediatas (éxito previo o bloqueos fatales)."""
        if report.decision == ExecutionDecision.ALREADY_FULFILLED:
            strategy_result = StrategyResult(
                strategy_name="None",
                pieces_processed=0,
                did_upload_bytes=False,
                is_completed=True,
                message="El archivo ya se encuentra 100% completado en este chat.",
            )
            self.ctx.observer.on_job_complete(job, snapshot_created=False)
            return JobExecutionResult(
                job=job,
                path=path,
                decision=report.decision,
                strategy_result=strategy_result,
                is_completed=True,
                snapshot_generated=False,
                message=strategy_result.message,
            )

        if report.decision == ExecutionDecision.PERMISSION_DENIED_CLEANUP:
            raise PermissionDeniedCleanupError(
                chat_id=self.ctx.tg_chat.id,
                reason=report.detail
                or "Permiso 'can_delete_messages' requerido para limpiar el canal.",
            )

        if report.decision == ExecutionDecision.INCOMPATIBLE_CAPACITY:
            raise IncompatibleCapacityError(
                job_chunk_size=job.chunk_size,
                account_max_size=account_max_size,
                is_premium=self.ctx.owner.is_premium,
            )

        return None

    def _prepare_job_state(
        self,
        job: Job,
        path: Path,
        report: InvestigationReport,
        force: bool,
        account_max_size: int,
    ) -> Job:
        """Aplica las acciones previas necesarias en Telegram o SQLite antes de subir."""
        # Caso A: Reubicación al fondo (Anti-Waste TOC)
        if report.decision == ExecutionDecision.RELOCATED_AND_RESUMED:
            logger.info(
                f"Job {job.id}: Ejecutando reubicación atómica de {len(report.valid_remotes)} trozos..."
            )
            self.ctx.observer.on_relocation_start(len(report.valid_remotes))
            ChunkRelocator.relocate(
                client=self.ctx.client,
                db=self.ctx.db,
                chat_id=self.ctx.tg_chat.id,
                remotes=report.valid_remotes,
            )
            return job

        # Caso B: Reinicio limpio con --force
        if (
            report.decision == ExecutionDecision.CLEAN_RESTART
            and force
            and job.payloads.count() > 0
        ):
            old_msg_ids = [r.message_id for r in report.valid_remotes]

            if old_msg_ids and not self.ctx.yes and self.ctx.confirm_cleanup:
                confirmed = self.ctx.confirm_cleanup(len(old_msg_ids), path.name)
                if not confirmed:
                    raise PipelineCancelledError(
                        f"Operación cancelada por el usuario para {path.name}."
                    )

            logger.info(
                f"Job {job.id}: Purgando intento previo para reinicio limpio con --force..."
            )
            if old_msg_ids:
                try:
                    self.ctx.client.delete_messages(
                        chat_id=self.ctx.tg_chat.id, message_ids=old_msg_ids
                    )
                except Exception as e:
                    logger.warning(f"Aviso limpiando mensajes viejos en Telegram: {e}")

            with db_transaction(self.ctx.db):
                source = job.source
                chat = job.chat
                job.purge(self.ctx.db)
                job = Job.formalize_intent(source, chat, chunk_size=account_max_size)

        return job

    def _execute_transfer(
        self,
        job: Job,
        report: InvestigationReport,
    ) -> StrategyResult:
        """Realiza la transferencia física o resuelve el sellado directo sin transferir bytes."""
        if report.decision == ExecutionDecision.SEAL_HEALED:
            logger.info(f"Job {job.id}: Sellando snapshot faltante (SEAL_HEALED)...")
            return StrategyResult(
                strategy_name="SealHealing",
                pieces_processed=len(report.valid_remotes),
                did_upload_bytes=False,
                is_completed=True,
                message="Todas las partes ya estaban en Telegram. Snapshot sellado exitosamente.",
            )

        # Único punto de subida física
        return self.uploader.upload(job, self.ctx)

    def _seal_job_snapshot(self, job: Job) -> tuple[bool, bool]:
        """Genera el snapshot, lo publica en Telegram y lo registra en el Ledger local."""
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
                client=self.ctx.client,
                chat_id=self.ctx.tg_chat.id,
                snapshot_path=temp_snapshot_file,
                manifest=manifest,
            )

            with db_transaction(self.ctx.db):
                job.set_snapshot_published(sent_msg.id, self.ctx.owner)

            ReceiptLedger.record_upload(
                file_path=job.path,
                chat_id=self.ctx.tg_chat.id,
                snapshot_message_id=sent_msg.id,
                chunk_size=job.chunk_size,
                total_parts=job.payloads.count(),
                md5=job.source.md5sum,
                chat_title=self.ctx.tg_chat.title,
            )

        return True, True

    def _apply_inter_job_pause(self) -> None:
        """Aplica la pausa de seguridad entre archivos comunicando la cuenta regresiva al observador."""
        pause_range = self.ctx.settings.upload_pause_range
        if not pause_range or pause_range == [0, 0]:
            return

        minutes = random.randint(min(pause_range), max(pause_range))
        seconds = minutes * 60

        if seconds > 0:
            self.ctx.observer.on_pause_start(seconds, "Inter-job")
            remaining = seconds
            while remaining > 0:
                self.ctx.observer.on_pause_tick(remaining, seconds)
                time.sleep(1)
                remaining -= 1
            self.ctx.observer.on_pause_end()
