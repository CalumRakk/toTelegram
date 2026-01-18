import logging
import time
from typing import TYPE_CHECKING, List, Optional, Tuple, cast

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Message  # type: ignore

from totelegram.core.enums import AvailabilityState
from totelegram.store.models import Job, Payload, RemotePayload, TelegramChat

logger = logging.getLogger(__name__)


class DiscoveryService:
    def __init__(self, client: "Client"):
        self.client = client

    def investigate(
        self, job: Job
    ) -> Tuple[AvailabilityState, Optional[List[RemotePayload]]]:
        """
        Analiza si el contenido del Job ya existe en el ecosistema.
        Retorna (Estado, Lista de RemotePayloads fuente para Forward).
        """
        source_file = job.source
        target_chat_id = job.chat.id

        # ¿Ya existe en el chat de destino? (FULFILLED)
        # Buscamos si hay algún Job previo del mismo SourceFile en este mismo Chat
        # que tenga todos sus RemotePayloads registrados.
        existing_remotes_in_target = self._get_complete_remote_set(
            source_file, target_chat_id
        )
        if existing_remotes_in_target:
            # ¿Siguen existiendo los mensajes?
            if self._validate_access_jit(existing_remotes_in_target):
                return AvailabilityState.FULFILLED, None

        # ¿Existe en algún otro chat de la DB? (RECOVERABLE)
        # Obtenemos todos los chats donde este archivo ha sido subido anteriormente
        potential_chats = (
            TelegramChat.select()
            .join(RemotePayload)
            .join(Payload)
            .join(Job)
            .where(Job.source == source_file)
            .where(TelegramChat.id != target_chat_id)
            .distinct()
        )

        for chat in potential_chats:
            remotes = self._get_complete_remote_set(source_file, chat.id)
            if remotes and self._validate_access_jit(remotes):
                logger.info(f"¡Smart Forwarding detectado! Fuente: Chat {chat.id}")
                return AvailabilityState.RECOVERABLE, remotes

        # ¿Existe pero no tenemos acceso? (RESTRICTED)
        # Si la DB dice que hay remotes pero no podemos acceder a ellos, no podemos subir.
        has_any_record = (
            RemotePayload.select()
            .join(Payload)
            .join(Job)
            .where(Job.source == source_file)
            .exists()
        )
        if has_any_record:
            return AvailabilityState.RESTRICTED, None

        return AvailabilityState.NEW, None

    def _get_complete_remote_set(
        self, source_file, chat_id
    ) -> Optional[List[RemotePayload]]:
        """
        Comprueba si existe el conjunto de fragmentos (RemotePayloads) completo en la DB.

        Es vital para Jobs CHUNKED: no podemos recuperar un archivo si le faltan trozos.
        """
        # Obtenemos el Job que sirvió de base para ese chat
        base_job = (
            Job.select().where(Job.source == source_file, Job.chat == chat_id).first()
        )

        if not base_job:
            return None

        # Contamos cuántos payloads debería tener según su estrategia
        # y cuántos RemotePayload hay realmente en la DB.
        expected_count = base_job.payloads.count()
        remotes = list(
            RemotePayload.select()
            .join(Payload)
            .where(Payload.job == base_job)
            .order_by(Payload.sequence_index)
        )

        if len(remotes) == expected_count and expected_count > 0:
            return remotes
        return None

    def _validate_access_jit(self, remotes: List[RemotePayload]) -> bool:
        """
        Pregunta a Telegram si el perfil actual puede ver los remotes especificados.
        """
        try:
            if not remotes:
                return False

            for sample in remotes:
                msg = cast(
                    "Message",
                    self.client.get_messages(
                        chat_id=sample.chat_id, message_ids=sample.message_id
                    ),
                )
                if msg is None or msg.empty:
                    return False

                time.sleep(0.5)
        except Exception as e:
            logger.debug(f"Fallo de validación JIT para chat {remotes[0].chat_id}: {e}")
        return False
