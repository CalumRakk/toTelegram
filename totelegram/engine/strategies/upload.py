import logging
import random
from typing import BinaryIO, List, cast

from pyrogram.types import Message

from totelegram.common.streams import ThrottledFile
from totelegram.database import db_transaction
from totelegram.engine.events import ProgressMultiplexer
from totelegram.engine.factory import PayloadStreamFactory
from totelegram.engine.strategies.base import JobStrategy
from totelegram.models import Job, Payload, RemotePayload
from totelegram.types import AvailabilityReport, StrategyResult, UploadContext

logger = logging.getLogger(__name__)


class CooperativeUploadStrategy(JobStrategy):
    """
    Estrategia de subida física cooperativa.
    Reclama atómicamente piezas libres del Job y las transmite con heartbeat activo y telemetría visual.
    """

    def execute(
        self,
        job: Job,
        ctx: UploadContext,
        report: AvailabilityReport,
    ) -> StrategyResult:
        with db_transaction(ctx.db):
            job.prepare_chunks(job.path, ctx.settings)

        pieces_uploaded = 0
        limit_bytes = ctx.settings.upload_limit_rate_kbps * 1024
        total_chunks = job.payloads.count()

        logger.info(
            f"Iniciando subida física para Job {job.id} ({job.source.path_str})"
        )

        while True:
            with ctx.coordinator.claim_next_payload(job, ctx.account_id) as claim:
                if claim is None:
                    logger.debug(
                        f"No hay más piezas pendientes/libres en Job {job.id}."
                    )
                    break

                payload = claim.payload
                current_part = payload.sequence_index + 1
                logger.info(
                    f"Cuenta {ctx.account_id} reclamó pieza {current_part}/{total_chunks} ({payload.filename})"
                )

                # Notificar al observer el inicio de la pieza
                ctx.observer.on_payload_start(payload, current_part, total_chunks)

                with PayloadStreamFactory.create_stream(
                    payload=payload,
                    source_path=job.path,
                    max_filename_len=ctx.settings.max_filename_length,
                ) as upload_stream:
                    stream_obj = ThrottledFile(
                        upload_stream.stream, speed_limit_bytes_per_s=limit_bytes
                    )

                    # Multiplexor: Despacha simultáneamente a Heartbeat (DB) y Observer (UI)
                    multiplexer = ProgressMultiplexer(
                        heartbeat=claim.heartbeat,
                        observer=ctx.observer,
                    )

                    with stream_obj as doc_stream:
                        doc_stream = cast(BinaryIO, doc_stream)
                        tg_message = cast(
                            Message,
                            ctx.client.send_document(
                                chat_id=ctx.tg_chat.id,
                                document=doc_stream,
                                file_name=upload_stream.filename,
                                caption=upload_stream.caption,
                                progress=multiplexer,
                                force_document=True,
                            ),
                        )

                    with db_transaction(ctx.db):
                        payload.md5sum = upload_stream.md5sum
                        payload.save(only=[Payload.md5sum, Payload.updated_at])
                        RemotePayload.register_upload(payload, tg_message, ctx.owner)

                    pieces_uploaded += 1
                    ctx.observer.on_payload_complete(payload)

                # Pausa INTRA-JOB (si aún quedan piezas pendientes en este Job)
                remaining_in_job = Payload.total_pending_for_job(job)
                if remaining_in_job > 0:
                    pause_seconds = self._calculate_pause_seconds(
                        ctx.settings.upload_pause_range
                    )
                    if pause_seconds > 0:
                        ctx.observer.on_pause_start(pause_seconds, "Intra-job")
                        claim.heartbeat.sleep(
                            pause_seconds,
                            on_tick=ctx.observer.on_pause_tick,
                        )
                        ctx.observer.on_pause_end()

        with db_transaction(ctx.db):
            pending_total = Payload.total_pending_for_job(job)
            is_completed = pending_total == 0
            if is_completed:
                job.set_uploaded()

        return StrategyResult(
            strategy_name="CooperativeUploadStrategy",
            pieces_processed=pieces_uploaded,
            did_upload_bytes=pieces_uploaded > 0,
            is_completed=is_completed,
            message=f"Subida cooperativa finalizada ({pieces_uploaded} piezas subidas).",
        )

    def _calculate_pause_seconds(self, pause_range: List[int]) -> int:
        if not pause_range or pause_range == [0, 0]:
            return 0
        min_p = min(pause_range)
        max_p = max(pause_range)
        minutes = random.randint(min_p, max_p)
        return minutes * 60
