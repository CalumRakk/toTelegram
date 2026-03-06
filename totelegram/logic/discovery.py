import logging
import math
import time
from typing import TYPE_CHECKING, List

import peewee

from totelegram.common.enums import AvailabilityState, JobStatus
from totelegram.common.types import AvailabilityReport
from totelegram.common.utils import batched
from totelegram.manager.models import Job, Payload, RemotePayload

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Message

logger = logging.getLogger(__name__)


class DiscoveryService:
    def __init__(self, client: "Client", db: peewee.Database):
        self.client = client
        self.db = db

    def investigate(self, job: Job) -> AvailabilityReport:
        """
        Analiza la disponibilidad del Job y devuelve un reporte con los recursos encontrados.
        """

        if self.is_fulfilled_local(job):
            return AvailabilityReport(state=AvailabilityState.FULFILLED)

        historical_jobs = self.get_historical_jobs(job)
        if historical_jobs.exists():
            remotes = self.get_remotes(historical_jobs)
            if remotes:
                return AvailabilityReport(
                    state=AvailabilityState.CAN_FORWARD, remotes=remotes
                )

        return AvailabilityReport(state=AvailabilityState.NEEDS_UPLOAD)

    def get_historical_jobs(self, job: Job) -> peewee.ModelSelect:
        return (
            Job.select()
            .where(
                (Job.source == job.source)
                & (Job.status == JobStatus.UPLOADED)
                & (Job.chat != job.chat)
            )
            .order_by(Job.updated_at.desc())  # type: ignore
        )

    def get_remotes(self, historical_jobs):
        for hist_job in historical_jobs:
            remotes = list(
                RemotePayload.select(RemotePayload, Payload)
                .join(Payload)
                .where(Payload.job == hist_job)
                .order_by(Payload.sequence_index)
            )

            expected_count = self._get_expected_count(hist_job)

            if len(remotes) == expected_count:
                if self._validate_jit_batch(remotes):
                    logger.info(f"Espejo íntegro: Job {hist_job.id}")
                    return remotes
        return None

    def is_fulfilled_local(self, job: Job) -> bool:
        """Verifica si el Job actual ya está completo en el destino."""

        local_remotes = list(
            RemotePayload.select()
            .join(Payload)
            .where(Payload.job == job)
            .order_by(Payload.sequence_index)
        )

        expected = self._get_expected_count(job)
        if len(local_remotes) == expected and expected > 0:
            return self._validate_jit_batch(local_remotes)
        return False

    def _validate_jit_batch(self, remotes: List[RemotePayload]) -> bool:
        """
        Verifica en Telegram si los mensajes de un Job siguen existiendo.
        Usa caché interna para evitar peticiones redundantes.
        """
        if not remotes:
            return False

        to_verify = [r for r in remotes if not r.is_fresh]

        if not to_verify:
            return True

        chat_id = to_verify[0].chat_id
        msg_ids = [r.message_id for r in to_verify]
        try:
            with self.db.atomic():
                for batch_ids in batched(msg_ids, 200):
                    messages = self.client.get_messages(chat_id, batch_ids)  # type: ignore
                    if not isinstance(messages, list):
                        messages: List["Message"]
                        messages = [messages]

                    for msg in messages:
                        # Si un solo mensaje del set falló, el espejo no es íntegro
                        if (
                            msg is None
                            or getattr(msg, "empty", True)
                            or not msg.document
                        ):
                            return False

                        remote = next(r for r in to_verify if r.message_id == msg.id)

                        # Verificación extra: ¿El tamaño coincide? (Anti-edición)
                        if msg.document.file_size != remote.payload.size:
                            return False

                        remote.mark_verified(msg)

                    time.sleep(0.5)
                return True

        except Exception as e:
            logger.debug(f"Error en validación JIT: {e}")
            return False

    def _get_expected_count(self, job: Job) -> int:
        # BUG: comprobar si matematicamente esta comprobacion funciona para las cintas.
        return math.ceil(job.source.size / job.config.tg_max_size)
