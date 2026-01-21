import logging
import math
import time
from typing import TYPE_CHECKING, Dict, List, Optional, cast

from pydantic import BaseModel

from totelegram.utils import batched

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Message  # type: ignore

from totelegram.core.enums import AvailabilityState, Strategy
from totelegram.store.models import Job, Payload, RemotePayload

logger = logging.getLogger(__name__)


class DiscoveryReport(BaseModel):
    state: AvailabilityState
    remotes: Optional[List[RemotePayload]] = []
    model_config = {"arbitrary_types_allowed": True}

    # @property
    # def is_redundant(self) -> bool:
    #     """
    #     Encapsula el concepto de: 'El archivo existe fuera
    #     del destino, ya sea como espejo o como puzzle'.
    #     """
    #     # Todo: Encapsula bien, pero me da la sensacion de caja negra.
    #     return self.state in [
    #         AvailabilityState.REMOTE_MIRROR,
    #         AvailabilityState.REMOTE_PUZZLE,
    #     ]


class DiscoveryService:
    def __init__(self, client: "Client"):
        self.client = client

    def investigate(self, job: Job):
        """
        Orquesta la investigación de redundancia siguiendo el principio de
        mínimo esfuerzo: Local -> Espejo Único -> Puzzle -> Nuevo.
        """

        if self._is_already_fulfilled(job):
            return DiscoveryReport(state=AvailabilityState.FULFILLED)

        global_remotes = self._query_global_records_db(job)
        if not global_remotes:
            return DiscoveryReport(state=AvailabilityState.SYSTEM_NEW)

        mirror_remotes = self._find_integrity_mirror(job, global_remotes)
        if mirror_remotes:
            return DiscoveryReport(
                state=AvailabilityState.REMOTE_MIRROR, remotes=mirror_remotes
            )

        puzzle_pieces = self._assemble_puzzle_pieces(job, global_remotes)
        if puzzle_pieces:
            return DiscoveryReport(
                state=AvailabilityState.REMOTE_PUZZLE, remotes=puzzle_pieces
            )

        return DiscoveryReport(state=AvailabilityState.REMOTE_RESTRICTED)

    def _collect_puzzle_pieces(self, job, remotes_by_chat):
        """Intenta recolectar todas las secuencias del archivo desde fuentes dispersas."""
        collected = {}  # seq_index -> RemotePayload
        for chat_id, remotes in remotes_by_chat.items():
            # Solo validamos si este chat nos aporta piezas que aún no tenemos
            valid_for_access = None

            for r in remotes:
                if r.payload.sequence_index not in collected:
                    # Validación perezosa: solo validamos el chat si tiene piezas útiles
                    if valid_for_access is None:
                        valid_for_access = self._validate_jit([r])

                    if valid_for_access:
                        collected[r.payload.sequence_index] = r

        # Devolvemos las piezas ordenadas por secuencia
        return (
            [collected[i] for i in sorted(collected.keys())]
            if len(collected) == self._get_expected_count(job)
            else []
        )

    def _validate_jit(self, remotes: List[RemotePayload]) -> bool:
        """
        Valida la existencia de TODAS las piezas en un chat, gestionando el límite
        de 200 mensajes por petición de la API de Telegram.
        """
        if not remotes:
            return False

        # Validamos que todos los remotes pertenezcan al mismo chat
        unique_chats = {r.chat_id for r in remotes}
        if len(unique_chats) > 1:
            # Si alguien intenta validar piezas de chats mezclados, es un error de lógica.
            logger.error(
                "Error de lógica: _validate_jit recibió piezas de múltiples chats."
            )
            return False
        from pyrogram.types import Message  # type: ignore

        # funcion auxilicar para validar un mensaje
        def is_valid(message: Message, remote_map: Dict[int, RemotePayload]) -> bool:
            """Devuelve True si el mensaje sigue accesible.

            Args:
                message (Message): El mensaje a validar
                remote_map (Dict[int, RemotePayload]): El mapa de ID:RemotePayload

            Logica:
            - El mensaje debe ser un documento
            - El tamaño del documento debe ser el mismo que el tamaño de la pieza
            - El mensaje no debe estar vacio

            """
            if message is None or getattr(message, "empty", True):
                return False

            # VALIDACIÓN ANTI-EDICIÓN
            expected = remote_map.get(message.id)
            if not expected:
                return False

            # ¿Sigue siendo un documento?
            if not message.document:
                logger.debug(f"El mensaje {m.id} ya no contiene un archivo.")
                return False

            # ¿El tamaño coincide al byte?
            if message.document.file_size != expected.payload.size:
                logger.debug(
                    f"Colisión detectada: El tamaño de {message.id} no coincide."
                )
                return False
            return True

        try:
            CHUNK_LIMIT = 200
            chat_id = remotes[0].chat_id
            msg_ids = [r.message_id for r in remotes]
            remote_map = {r.message_id: r for r in remotes}

            for batch_ids in batched(msg_ids, CHUNK_LIMIT):
                messages = self.client.get_messages(
                    chat_id=chat_id, message_ids=batch_ids
                )

                # Pyrogram puede devolver un Message o una lista.
                if not isinstance(messages, list):
                    messages = cast(List[Message], [messages])

                for m in messages:
                    if not is_valid(m, remote_map):
                        return False

                time.sleep(0.5)
            return True

        except Exception as e:
            logger.debug(f"JIT Identity check failed: {e}")
            return False

    def _is_already_fulfilled(self, job: Job) -> bool:
        """Comprueba si el chat actual ya posee el archivo completo y accesible."""
        local_remotes = self._get_remotes_for_chat(job, job.chat.id)

        expected = self._get_expected_count(job)

        if len(local_remotes) == expected and expected > 0:
            return self._validate_jit(local_remotes)
        return False

    def _query_global_records_db(self, job: Job) -> List[RemotePayload]:
        return list(
            RemotePayload.select()
            .join(Payload)
            .join(Job)
            .where(Job.source == job.source, RemotePayload.chat_id != job.chat.id)
        )

    def _get_remotes_for_chat(self, source_file, chat_id) -> List[RemotePayload]:
        """
        Obtiene los RemotePayloads de la base de datos para un chat.

        """
        # Obtenemos el Job que sirvió de base para ese chat
        base_job = (
            Job.select().where(Job.source == source_file, Job.chat == chat_id).first()
        )

        if not base_job:
            return []

        # Contamos cuántos payloads debería tener según su estrategia
        # y cuántos RemotePayload hay realmente en la DB.
        expected_count = self._get_expected_count(base_job)
        remotes = list(
            RemotePayload.select()
            .join(Payload)
            .where(Payload.job == base_job)
            .order_by(Payload.sequence_index)
        )

        if len(remotes) == expected_count and expected_count > 0:
            return remotes
        return []

    def _find_integrity_mirror(
        self, job: Job, pool: List[RemotePayload]
    ) -> Optional[List[RemotePayload]]:
        """Busca si existe AL MENOS un chat que contenga el set completo de partes."""

        by_chat = {}
        for r in pool:
            by_chat.setdefault(r.chat_id, []).append(r)

        expected_count = self._get_expected_count(job)
        for chat_id, remotes in by_chat.items():
            if len(remotes) == expected_count:
                if self._validate_jit(remotes):
                    return remotes
        return None

    def _assemble_puzzle_pieces(
        self, job: Job, pool: List[RemotePayload]
    ) -> List[RemotePayload]:
        """
        Intenta reconstruir el archivo completo recolectando piezas de diferentes chats.

        Este método es el corazón de la "Inteligencia Colectiva". Si el archivo no está
        completo en ningún chat individual (Mirror), este método busca si sumando los
        trozos dispersos por todo el ecosistema podemos llegar al 100% del archivo.

        Lógica:
        1. Agrupa todas las piezas conocidas por chat.
        2. Recorre cada chat buscando piezas que "rellenen los huecos" que aún tenemos.
        3. Si al final el mapa de piezas está lleno, tenemos el puzzle completo.
        """
        # Organiza todas las piezas disponibles por su ubicación (Chat ID)
        parts_by_location = {}
        for remote in pool:
            parts_by_location.setdefault(remote.chat_id, []).append(remote)

        collected_puzzle = {}
        total_parts_needed = self._get_expected_count(job)

        # Va a cada chat y recoge las piezas que aún no tenemos
        for chat_id, remotes_in_this_chat in parts_by_location.items():

            # Filtra las piezas que aún no tenemos
            new_useful_pieces = [
                r
                for r in remotes_in_this_chat
                if r.payload.sequence_index not in collected_puzzle
            ]

            # Si este chat tiene piezas nuevas, verificamos si tenemos acceso
            if new_useful_pieces:
                if self._validate_jit(remotes_in_this_chat):
                    for piece in new_useful_pieces:
                        collected_puzzle[piece.payload.sequence_index] = piece

            # Si reunimos todas las piezas, podemos salir temprano
            if len(collected_puzzle) == total_parts_needed:
                break

        # Verificación final: ¿Conseguimos todas las piezas del 0 al N?
        if len(collected_puzzle) == total_parts_needed:
            # Devolvemos la lista ordenada (Parte 1, Parte 2, Parte 3...)
            return [collected_puzzle[i] for i in sorted(collected_puzzle.keys())]

        # Si llegamos aquí, faltan piezas en la red global para completar este archivo
        return []

    def _get_expected_count(self, job: Job) -> int:
        """
        Determina cuántas piezas esperamos para este archivo.
        Si ya existen en DB, usa ese valor. Si no, lo calcula según el contrato.
        """
        db_count = job.payloads.count()
        if db_count > 0:
            return db_count

        if job.strategy == Strategy.SINGLE:
            return 1

        # TODO : encapsular la lógica de CHUNKED si es posible.
        return math.ceil(job.source.size / job.config.tg_max_size)
