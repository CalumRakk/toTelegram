import logging
import random
import time
from pathlib import Path

from totelegram.database import db_transaction
from totelegram.engine.planner import JobPlanner
from totelegram.engine.relocator import ChunkRelocator
from totelegram.engine.strategies.upload import CooperativeUploadStrategy
from totelegram.models import Job
from totelegram.packaging.publisher import SnapshotPublicationError, SnapshotPublisher
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import ExecutionDecision
from totelegram.types import (
    IncompatibleCapacityError,
    JobExecutionResult,
    PermissionDeniedCleanupError,
    StrategyResult,
    UploadContext,
)

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

            # 3. Despacho determinista de los 7 estados resolutivos

            # CASO A: Ya completado en el canal (0 bytes transferidos)
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

            # CASO B: Bloqueo por falta de permisos de eliminación
            if report.decision == ExecutionDecision.PERMISSION_DENIED_CLEANUP:
                raise PermissionDeniedCleanupError(
                    chat_id=self.ctx.tg_chat.id,
                    reason=report.detail
                    or "Permiso 'can_delete_messages' requerido para limpiar el canal.",
                )

            # CASO C: Bloqueo por incompatibilidad de capacidad (ej: Job 4GB con cuenta 2GB)
            if report.decision == ExecutionDecision.INCOMPATIBLE_CAPACITY:
                raise IncompatibleCapacityError(
                    job_chunk_size=job.chunk_size,
                    account_max_size=account_max_size,
                    is_premium=self.ctx.owner.is_premium,
                )

            # CASO D: Todas las piezas ya estaban en Telegram, solo falta sellar Snapshot
            if report.decision == ExecutionDecision.SEAL_HEALED:
                logger.info(
                    f"Job {job.id}: Sellando snapshot faltante (SEAL_HEALED)..."
                )
                strategy_result = StrategyResult(
                    strategy_name="SealHealing",
                    pieces_processed=len(report.valid_remotes),
                    did_upload_bytes=False,
                    is_completed=True,
                    message="Todas las partes ya estaban en Telegram. Snapshot sellado exitosamente.",
                )

            # CASO E: Reubicación al fondo con copy_message + delete (Anti-Waste TOC)
            elif report.decision == ExecutionDecision.RELOCATED_AND_RESUMED:
                logger.info(
                    f"Job {job.id}: Ejecutando reubicación atómica de {len(report.valid_remotes)} trozos..."
                )
                ChunkRelocator.relocate(
                    client=self.ctx.client,
                    db=self.ctx.db,
                    chat_id=self.ctx.tg_chat.id,
                    remotes=report.valid_remotes,
                )
                # Una vez reubicadas las partes viejas al fondo, subimos las faltantes
                strategy = CooperativeUploadStrategy()
                strategy_result = strategy.execute(job, self.ctx, report)

            # CASO F: Reinicio limpio (--force ante cambio de corte incompatible)
            elif (
                report.decision == ExecutionDecision.CLEAN_RESTART
                and force
                and job.payloads.count() > 0
            ):
                logger.info(
                    f"Job {job.id}: Purgando intento previo para reinicio limpio con --force..."
                )
                # 1. Eliminar mensajes viejos de Telegram
                old_msg_ids = [r.message_id for r in report.valid_remotes]
                if old_msg_ids:
                    try:
                        self.ctx.client.delete_messages(
                            chat_id=self.ctx.tg_chat.id, message_ids=old_msg_ids
                        )
                    except Exception as e:
                        logger.warning(
                            f"Aviso limpiando mensajes viejos en Telegram: {e}"
                        )

                # 2. Purgar Job en SQLite y recrearlo con el nuevo chunk_size adecuado
                with db_transaction(self.ctx.db):
                    source = job.source
                    chat = job.chat
                    job.purge(self.ctx.db)
                    job = Job.formalize_intent(
                        source, chat, chunk_size=account_max_size
                    )

                strategy = CooperativeUploadStrategy()
                strategy_result = strategy.execute(job, self.ctx, report)

            # CASO G: Reanudación estándar en su sitio o subida nueva
            else:
                strategy = CooperativeUploadStrategy()
                strategy_result = strategy.execute(job, self.ctx, report)

            # 4. Sellado de Snapshot si todas las piezas quedaron subidas
            is_completed = False
            snapshot_generated = False

            if job.is_completed_local:
                is_completed = True

                if job.remote_snapshot is None:
                    try:
                        manifest, snapshot_path = SnapshotService.generate_snapshot(job)
                        snapshot_generated = True

                        sent_msg = SnapshotPublisher.publish(
                            client=self.ctx.client,
                            chat_id=self.ctx.tg_chat.id,
                            snapshot_path=snapshot_path,
                            manifest=manifest,
                        )

                        with db_transaction(self.ctx.db):
                            job.set_snapshot_published(sent_msg.id, self.ctx.owner)
                            logger.info(
                                f"Job {job.id} sellado con RemoteSnapshot Msg ID: {sent_msg.id}"
                            )

                    except SnapshotPublicationError as e:
                        logger.error(f"Fallo al publicar snapshot en Telegram: {e}")
                    except Exception:
                        logger.exception(
                            f"Error inesperado en cierre de snapshot para Job {job.id}"
                        )

            self.ctx.observer.on_job_complete(job, snapshot_generated)

        # Pausa inter-job si se transmitieron bytes reales
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
