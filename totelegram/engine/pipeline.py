import logging
import random
import tempfile
import time
from pathlib import Path

from totelegram.database import db_transaction
from totelegram.engine.planner import JobPlanner
from totelegram.engine.relocator import ChunkRelocator
from totelegram.engine.strategies.upload import CooperativeUploadStrategy
from totelegram.models import Job
from totelegram.packaging.ledger import ReceiptLedger
from totelegram.packaging.publisher import SnapshotPublisher
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

        # Planificación de la intención
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
            # Investigación física JIT en el chat destino
            report = self.ctx.discovery.investigate(
                job=job,
                force=force,
                account_max_chunk_size=account_max_size,
            )

            if report.potential_duplicates:
                self.ctx.observer.on_potential_duplicates(report.potential_duplicates)

            logger.info(f"Decisión de ejecución resuelta: {report.decision.value}")

            # Despacho determinista de los 7 estados resolutivos

            # Ya completado en el canal (0 bytes transferidos)
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

            # Bloqueo por falta de permisos de eliminación
            if report.decision == ExecutionDecision.PERMISSION_DENIED_CLEANUP:
                raise PermissionDeniedCleanupError(
                    chat_id=self.ctx.tg_chat.id,
                    reason=report.detail
                    or "Permiso 'can_delete_messages' requerido para limpiar el canal.",
                )

            # Bloqueo por incompatibilidad de capacidad (ej: Job 4GB con cuenta 2GB)
            if report.decision == ExecutionDecision.INCOMPATIBLE_CAPACITY:
                raise IncompatibleCapacityError(
                    job_chunk_size=job.chunk_size,
                    account_max_size=account_max_size,
                    is_premium=self.ctx.owner.is_premium,
                )

            # Todas las piezas ya estaban en Telegram, solo falta sellar Snapshot
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

            # Reubicación al fondo con copy_message + delete (Anti-Waste TOC)
            elif report.decision == ExecutionDecision.RELOCATED_AND_RESUMED:
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
                strategy = CooperativeUploadStrategy()
                strategy_result = strategy.execute(job, self.ctx, report)

            # Reinicio limpio (--force ante cambio de corte incompatible)
            elif (
                report.decision == ExecutionDecision.CLEAN_RESTART
                and force
                and job.payloads.count() > 0
            ):
                old_msg_ids = [r.message_id for r in report.valid_remotes]

                # Si hay mensajes en Telegram y no se activó --yes, solicitar confirmación
                if old_msg_ids and not self.ctx.yes and self.ctx.confirm_cleanup:
                    confirmed = self.ctx.confirm_cleanup(len(old_msg_ids), path.name)
                    if not confirmed:
                        from totelegram.types import PipelineCancelledError

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
                        logger.warning(
                            f"Aviso limpiando mensajes viejos en Telegram: {e}"
                        )

                # Purgar Job en SQLite y recrearlo con el nuevo chunk_size adecuado
                with db_transaction(self.ctx.db):
                    source = job.source
                    chat = job.chat
                    job.purge(self.ctx.db)
                    job = Job.formalize_intent(
                        source, chat, chunk_size=account_max_size
                    )

                strategy = CooperativeUploadStrategy()
                strategy_result = strategy.execute(job, self.ctx, report)

            # Reanudación estándar en su sitio o subida nueva
            else:
                strategy = CooperativeUploadStrategy()
                strategy_result = strategy.execute(job, self.ctx, report)

            # Sellado de Snapshot si todas las piezas quedaron subidas
            is_completed = False
            snapshot_generated = False

            if job.is_completed_local:
                is_completed = True

                if job.remote_snapshot is None:
                    # Crear directorio temporal aislado para el staging
                    with tempfile.TemporaryDirectory() as staging_dir:
                        staging_path = Path(staging_dir)
                        manifest, temp_snapshot_file = (
                            SnapshotService.generate_snapshot(
                                job, staging_dir=staging_path
                            )
                        )

                        # Publicar el snapshot físico en Telegram
                        sent_msg = SnapshotPublisher.publish(
                            client=self.ctx.client,
                            chat_id=self.ctx.tg_chat.id,
                            snapshot_path=temp_snapshot_file,
                            manifest=manifest,
                        )

                        # Persistir en SQLite
                        with db_transaction(self.ctx.db):
                            job.set_snapshot_published(sent_msg.id, self.ctx.owner)

                        # Registrar en el Recibo Agregado Local (.totelegram.json)
                        ReceiptLedger.record_upload(
                            file_path=job.path,
                            chat_id=self.ctx.tg_chat.id,
                            snapshot_message_id=sent_msg.id,
                            chunk_size=job.chunk_size,
                            total_parts=job.payloads.count(),
                            md5=job.source.md5sum,
                            chat_title=self.ctx.tg_chat.title,
                        )
                        snapshot_generated = True

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
