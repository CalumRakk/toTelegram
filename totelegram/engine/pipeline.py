import logging
import random
import time
from pathlib import Path

from totelegram.database import db_transaction
from totelegram.engine.planner import JobPlanner
from totelegram.engine.strategies.base import StrategyResolver
from totelegram.models import Payload
from totelegram.packaging.publisher import SnapshotPublicationError, SnapshotPublisher
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

            # Notificación heurística de posibles duplicados en el chat
            if report.potential_duplicates:
                self.ctx.observer.on_potential_duplicates(report.potential_duplicates)

            strategy = StrategyResolver.resolve(report)
            logger.info(f"Estrategia seleccionada: {strategy.__class__.__name__}")
            strategy_result = strategy.execute(job, self.ctx, report)

            is_completed = False
            snapshot_generated = False

            # Verificamos si todas las partes físicas están en Telegram
            pending_pieces = Payload.total_pending_for_job(job)

            if pending_pieces == 0:
                is_completed = True

                # IDEMPOTENCIA: Si el snapshot aún no se ha publicado en Telegram
                if job.snapshot_message_id is None:
                    try:
                        # Generar snapshot local (.json.xz o .ttape)
                        manifest, snapshot_path = SnapshotService.generate_snapshot(job)
                        snapshot_generated = True

                        # Publicar snapshot en Telegram con caption estructurado
                        self.ctx.observer.on_pause_start(
                            0, "Publicando snapshot en Telegram"
                        )  # Notificación visual
                        sent_msg = SnapshotPublisher.publish(
                            client=self.ctx.client,
                            chat_id=self.ctx.tg_chat.id,
                            snapshot_path=snapshot_path,
                            manifest=manifest,
                        )

                        # Transacción atómica: vincular snapshot y sellar Job
                        with db_transaction(self.ctx.db):
                            job.set_snapshot_published(sent_msg.id)
                            logger.info(
                                f"Job {job.id} completado y sellado con snapshot Msg ID: {sent_msg.id}"
                            )

                    except SnapshotPublicationError as e:
                        logger.error(f"Fallo al publicar snapshot en Telegram: {e}")
                        # Los chunks ya están en Telegram y DB; en la próxima ejecución solo reintentará publicar el snapshot
                    except Exception:
                        logger.exception(
                            f"Error inesperado en cierre de snapshot para Job {job.id}"
                        )
                else:
                    # El snapshot ya estaba publicado (ej. reanudación o re-ejecución)
                    if job.status != JobStatus.UPLOADED:
                        with db_transaction(self.ctx.db):
                            job.set_uploaded()

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
