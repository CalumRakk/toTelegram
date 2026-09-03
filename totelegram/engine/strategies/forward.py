import logging
import time
from typing import cast

from pyrogram.types import Message

from totelegram.database import db_transaction
from totelegram.engine.factory import PayloadStreamFactory
from totelegram.engine.strategies.base import JobStrategy
from totelegram.models import Job, RemotePayload
from totelegram.types import AvailabilityReport, StrategyResult, UploadContext

logger = logging.getLogger(__name__)


class SmartForwardStrategy(JobStrategy):
    """Estrategia de reenvío inteligente (Zero-byte upload) aprovechando espejos existentes."""

    def execute(
        self,
        job: Job,
        ctx: UploadContext,
        report: AvailabilityReport,
    ) -> StrategyResult:
        if not report.remotes:
            raise ValueError(
                "SmartForwardStrategy requiere una lista de remotos válidos en el reporte."
            )

        mirrors = {r.payload.sequence_index: r for r in report.remotes}
        logger.info(
            f"Iniciando Smart Forward para Job {job.id} desde {len(mirrors)} partes espejo."
        )

        with db_transaction(ctx.db):
            job_adopted = job.adopt_job(report.remotes[0].payload.job)
            payloads = job_adopted.prepare_chunks(job_adopted.source.path, ctx.settings)

        pieces_forwarded = 0
        for payload in payloads:
            if payload.has_remote:
                continue

            remote_mirror = mirrors.get(payload.sequence_index)
            if not remote_mirror:
                raise ValueError(
                    f"No se encontró espejo para la secuencia {payload.sequence_index}"
                )

            filename, caption = PayloadStreamFactory.resolve_naming(
                payload, ctx.settings.max_filename_length
            )

            # Reenvío de documento mediante file_id
            tg_message = cast(
                Message,
                ctx.client.send_document(
                    chat_id=ctx.tg_chat.id,
                    document=remote_mirror.message.document.file_id,
                    file_name=filename,
                    caption=caption,
                ),
            )

            with db_transaction(ctx.db):
                RemotePayload.register_upload(payload, tg_message, ctx.owner)

            pieces_forwarded += 1
            time.sleep(0.5)  # Pausa breve anti-flood

        with db_transaction(ctx.db):
            job_adopted.set_uploaded()

        return StrategyResult(
            strategy_name="SmartForwardStrategy",
            pieces_processed=pieces_forwarded,
            did_upload_bytes=False,
            is_completed=True,
            message=f"Smart Forward completado ({pieces_forwarded} partes vinculadas).",
        )
