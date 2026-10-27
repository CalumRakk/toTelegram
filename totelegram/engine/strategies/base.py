import abc
import logging

from totelegram.models import Job
from totelegram.types import InvestigationReport, StrategyResult, UploadContext

logger = logging.getLogger(__name__)


class JobStrategy(abc.ABC):
    """Interfaz base para estrategias de ejecución física de un Job."""

    @abc.abstractmethod
    def execute(
        self,
        job: Job,
        ctx: UploadContext,
        report: InvestigationReport,
    ) -> StrategyResult:
        """Ejecuta la transferencia de piezas sobre el Job."""
