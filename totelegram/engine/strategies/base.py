import abc
import logging

from totelegram.models import Job
from totelegram.schemas import AvailabilityState
from totelegram.types import AvailabilityReport, StrategyResult, UploadContext

logger = logging.getLogger(__name__)


class JobStrategy(abc.ABC):
    """Interfaz base para las estrategias de ejecución de un Job."""

    @abc.abstractmethod
    def execute(
        self,
        job: Job,
        ctx: UploadContext,
        report: AvailabilityReport,
    ) -> StrategyResult:
        """
        Ejecuta la estrategia concreta sobre el Job.
        Retorna un StrategyResult con los detalles de lo ocurrido.
        """
        pass


class StrategyResolver:
    """Fábrica para resolver la estrategia adecuada según el estado de disponibilidad."""

    @staticmethod
    def resolve(report: AvailabilityReport) -> JobStrategy:
        from totelegram.engine.strategies.forward import SmartForwardStrategy
        from totelegram.engine.strategies.fulfilled import FulfilledStrategy
        from totelegram.engine.strategies.upload import CooperativeUploadStrategy

        if report.state == AvailabilityState.FULFILLED:
            return FulfilledStrategy()

        if report.state == AvailabilityState.CAN_FORWARD:
            return SmartForwardStrategy()

        return CooperativeUploadStrategy()
