import logging
import random
import time
from pathlib import Path

from totelegram.database import db_transaction
from totelegram.engine.planner import JobPlanner
from totelegram.engine.strategies.base import StrategyResolver
from totelegram.models import Payload
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import JobStatus
from totelegram.types import JobExecutionResult, UploadContext

logger = logging.getLogger(__name__)


class JobPipeline:
    """
    Orquestador de alto nivel para el procesamiento de archivos y carpetas.
    Ejecuta el ciclo de vida de forma estrictamente declarativa y desacoplada.
    """

    def __init__(self, ctx: UploadContext):
        self.ctx = ctx

    def process(
        self,
        path: Path,
        is_last_in_batch: bool = False,
        force: bool = False,
        auto_truncate: bool = False,
    ) -> JobExecutionResult:
        logger.info(f"=== Iniciando Pipeline para: {path.name} ===")

        job = JobPlanner.plan(
            path=path,
            ctx=self.ctx,
            force=force,
            auto_truncate=auto_truncate,
        )

        total_pieces = job.payloads.count()
        self.ctx.observer.on_job_start(job, total_pieces)

        with self.ctx.coordinator.guard_account(self.ctx.account_id):
            report = self.ctx.discovery.investigate(job)
            strategy = StrategyResolver.resolve(report)
            logger.info(f"Estrategia seleccionada: {strategy.__class__.__name__}")
            strategy_result = strategy.execute(job, self.ctx, report)

            is_completed = False
            snapshot_generated = False

            with db_transaction(self.ctx.db):
                pending_pieces = Payload.total_pending_for_job(job)
                if pending_pieces == 0:
                    is_completed = True
                    if job.status != JobStatus.UPLOADED:
                        job.set_uploaded()

            if is_completed:
                try:
                    SnapshotService.generate_snapshot(job)
                    snapshot_generated = True
                except Exception as e:
                    logger.error(
                        f"Error generando snapshot para Job {job.id}: {e}",
                        exc_info=True,
                    )

            self.ctx.observer.on_job_complete(job, snapshot_generated)

        # Pausa INTER-JOB
        if not is_last_in_batch and strategy_result.did_upload_bytes:
            self._apply_inter_job_pause()

        return JobExecutionResult(
            job=job,
            path=path,
            strategy_result=strategy_result,
            is_completed=is_completed,
            snapshot_generated=snapshot_generated,
            message=strategy_result.message,
        )

    def _apply_inter_job_pause(self):
        """Aplica la pausa entre archivos del lote notificando al observer segundo a segundo."""
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
