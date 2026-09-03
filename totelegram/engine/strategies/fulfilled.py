import logging

from totelegram.database import db_transaction
from totelegram.engine.strategies.base import JobStrategy
from totelegram.models import Job
from totelegram.schemas import JobStatus
from totelegram.types import AvailabilityReport, StrategyResult, UploadContext

logger = logging.getLogger(__name__)


class FulfilledStrategy(JobStrategy):
    """Estrategia para recursos que ya existen íntegros en el destino."""

    def execute(
        self,
        job: Job,
        ctx: UploadContext,
        report: AvailabilityReport,
    ) -> StrategyResult:
        logger.info(
            f"Recurso {job.source.path_str} ya disponible en destino (Job {job.id})."
        )

        if job.status != JobStatus.UPLOADED:
            with db_transaction(ctx.db):
                job.set_uploaded()

        return StrategyResult(
            strategy_name="FulfilledStrategy",
            pieces_processed=0,
            did_upload_bytes=False,
            is_completed=True,
            message=f"'{job.path.name}' ya está disponible en el destino.",
        )
