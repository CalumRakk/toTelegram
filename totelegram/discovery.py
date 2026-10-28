import logging
import re
import time
from typing import TYPE_CHECKING, List, cast

import peewee
from pyrogram import enums
from pyrogram.errors import RPCError

from totelegram.common.helpers import batched
from totelegram.database import db_transaction
from totelegram.models import Job, Payload, RemotePayload
from totelegram.packaging.importer import SnapshotImporter
from totelegram.schemas import ExecutionDecision
from totelegram.telegram.access import ChatAccessService
from totelegram.types import DuplicateCandidate, InvestigationReport

if TYPE_CHECKING:
    from pyrogram.client import Client

logger = logging.getLogger(__name__)

HEURISTIC_DUPLICATE_MIN_SIZE = 20 * 1024 * 1024


class DiscoveryService:
    def __init__(self, client: "Client", db: peewee.Database):
        self.client = client
        self.db = db
        self.access_service = ChatAccessService(client)

    def investigate(
        self,
        job: Job,
        force: bool = False,
        account_max_chunk_size: int | None = None,
    ) -> InvestigationReport:
        """
        Analiza el estado físico del Job exclusivamente en su chat de destino.
        Resuelve la decisión preliminar según el árbol de estados.
        """
        logger.info(
            f"Investigando disponibilidad de '{job.source.path.name}' "
            f"(MD5: {job.source.md5sum[:12]}...) en chat {job.chat.id}"
        )

        potential_duplicates = self._search_potential_duplicates(job)

        # ¿Existe ya un Snapshot completado en este chat?
        if self._is_snapshot_fulfilled_in_chat(job):
            if not force:
                logger.info(f"Job {job.id} ya está completado en el canal de destino.")
                return InvestigationReport(
                    decision=ExecutionDecision.ALREADY_FULFILLED,
                    total_parts=job.total_expected_parts,
                    potential_duplicates=potential_duplicates,
                    detail="El archivo ya se encuentra 100% completado en este chat.",
                )
            else:
                logger.info("Snapshot existente ignorado porque se especificó --force.")

        # Comprobar piezas existentes del Job en BD y validar su existencia física JIT
        local_remotes = list(
            RemotePayload.select(RemotePayload, Payload)
            .join(Payload)
            .where((Payload.job == job) & (RemotePayload.is_orphaned == False))
            .order_by(Payload.sequence_index)
        )

        valid_remotes: List[RemotePayload] = []
        orphaned_remotes: List[RemotePayload] = []

        if local_remotes:
            valid_remotes, orphaned_remotes = self._validate_jit_batch(local_remotes)

        expected_count = self._get_expected_count(job)
        valid_count = len(valid_remotes)

        # ¿Están todas las partes físicas subidas pero falta el snapshot? (SEAL_HEALED)
        if valid_count == expected_count and expected_count > 0:
            logger.info(
                f"Job {job.id}: Todas las piezas ({valid_count}/{expected_count}) "
                "están en Telegram. Requiere únicamente sellado de snapshot."
            )
            return InvestigationReport(
                decision=ExecutionDecision.SEAL_HEALED,
                valid_remotes=valid_remotes,
                total_parts=expected_count,
                potential_duplicates=potential_duplicates,
                detail="Todas las partes físicas ya están en Telegram. Solo falta sellar el Snapshot.",
            )

        # Si hay partes parciales (subida a medias):
        if valid_count > 0:
            # A) Comprobación de corte incompatible (ej. Job de 4 GB con cuenta degradada a 2 GB)
            if account_max_chunk_size and job.chunk_size > account_max_chunk_size:
                if not force:
                    logger.warning(
                        f"Job {job.id} exige bloques de {job.chunk_size // (1024 * 1024)} MB, "
                        f"pero la cuenta solo soporta {account_max_chunk_size // (1024 * 1024)} MB."
                    )
                    return InvestigationReport(
                        decision=ExecutionDecision.INCOMPATIBLE_CAPACITY,
                        valid_remotes=valid_remotes,
                        orphaned_remotes=orphaned_remotes,
                        total_parts=expected_count,
                        potential_duplicates=potential_duplicates,
                        detail="El corte previo del archivo supera el límite de subida de la cuenta activa.",
                    )
                else:
                    # Con --force, el usuario desea descartar el corte incompatible y reiniciar en 2GB.
                    # Requiere permiso para eliminar los trozos de 4GB viejos de Telegram.
                    if not self.access_service.can_delete_messages(job.chat.id):
                        return InvestigationReport(
                            decision=ExecutionDecision.PERMISSION_DENIED_CLEANUP,
                            valid_remotes=valid_remotes,
                            total_parts=expected_count,
                            detail="Faltan permisos para eliminar los trozos viejos con corte incompatible.",
                        )
                    return InvestigationReport(
                        decision=ExecutionDecision.CLEAN_RESTART,
                        valid_remotes=valid_remotes,
                        total_parts=expected_count,
                        potential_duplicates=potential_duplicates,
                        detail="Reinicio limpio: se eliminarán partes de 4GB viejas y se reanudará en 2GB.",
                    )

            # B) Corte compatible con --force (Anti-Waste TOC): Reubicar al fondo
            if force:
                if not self.access_service.can_delete_messages(job.chat.id):
                    logger.warning(
                        f"No se puede reubicar trozos en chat {job.chat.id}: falta permiso can_delete_messages."
                    )
                    return InvestigationReport(
                        decision=ExecutionDecision.PERMISSION_DENIED_CLEANUP,
                        valid_remotes=valid_remotes,
                        total_parts=expected_count,
                        detail="Faltan permisos para eliminar los trozos antiguos al reordenar el chat.",
                    )

                logger.info(
                    f"Job {job.id} a medias ({valid_count}/{expected_count}) con --force: "
                    "Reubicación atómica al fondo del chat (Anti-Waste TOC)."
                )
                return InvestigationReport(
                    decision=ExecutionDecision.RELOCATED_AND_RESUMED,
                    valid_remotes=valid_remotes,
                    orphaned_remotes=orphaned_remotes,
                    total_parts=expected_count,
                    potential_duplicates=potential_duplicates,
                    detail="Subida a medias con corte compatible y --force. Se reubicarán trozos al fondo.",
                )

            # C) Corte compatible sin --force: Reanudar en el sitio
            return InvestigationReport(
                decision=ExecutionDecision.RESUMED_IN_PLACE,
                valid_remotes=valid_remotes,
                orphaned_remotes=orphaned_remotes,
                total_parts=expected_count,
                potential_duplicates=potential_duplicates,
                detail="Subida incompleta compatible. Reanudando partes faltantes en su sitio.",
            )

        # Sin partes previas válidas -> Subida limpia desde cero
        return InvestigationReport(
            decision=ExecutionDecision.CLEAN_RESTART,
            orphaned_remotes=orphaned_remotes,
            total_parts=expected_count,
            potential_duplicates=potential_duplicates,
            detail="No hay partes previas válidas. Requiere subida limpia.",
        )

    def _is_snapshot_fulfilled_in_chat(self, job: Job) -> bool:
        """Verifica si existe un Snapshot válido y activo en este chat (DB + Telegram JIT)."""
        if job.is_completed_local and job.has_remote_snapshot:
            # Si el job o el snapshot remoto fueron verificados recientemente, es válido de inmediato
            if job.is_fresh() or (job.remote_snapshot and job.remote_snapshot.is_fresh):
                return True
            if self._validate_job_snapshot_jit(job):
                return True

        return self._discover_and_hydrate_jit(job)

    def _discover_and_hydrate_jit(self, job: Job) -> bool:
        """Busca el snapshot del recurso en el chat destino en Telegram. Si lo encuentra, lo asimila."""
        from totelegram.telegram.captions import ManifestCaption

        target_short_hash = job.source.short_hash
        query = f"#src_{target_short_hash}"

        try:
            from pyrogram.types import Message

            found_messages = cast(
                List[Message],
                list(
                    self.client.search_messages(
                        chat_id=job.chat.id,
                        query=query,
                        filter=enums.MessagesFilter.DOCUMENT,
                        limit=5,
                    )
                ),
            )

            for m in found_messages:
                if not m.document or not m.caption:
                    continue
                parsed = ManifestCaption.parse(m.caption)
                if parsed and parsed.source_short_hash == target_short_hash:
                    return SnapshotImporter.rehydrate_from_message(
                        client=self.client,
                        message=m,
                        db=self.db,
                    )

            return False
        except RPCError as e:
            logger.warning(f"Error de Telegram durante búsqueda JIT: {e}")
            return False
        except Exception:
            logger.exception("Excepción inesperada en búsqueda JIT de snapshot")
            return False

    def _validate_job_snapshot_jit(self, job: Job) -> bool:
        """Comprueba si el mensaje del snapshot sigue existiendo en Telegram."""
        remote_snap = job.remote_snapshot
        if not remote_snap:
            return False

        try:
            from pyrogram.types import Message

            msg = cast(
                Message | None,
                self.client.get_messages(job.chat.id, remote_snap.message_id),
            )

            # Usamos `is True` estricto para no confundir MagicMocks con mensajes vacíos
            if (
                msg is None
                or getattr(msg, "empty", False) is True
                or not getattr(msg, "document", None)
            ):
                with db_transaction(self.db):
                    remote_snap.mark_orphaned()
                return False

            with db_transaction(self.db):
                remote_snap.mark_verified()
                job.mark_verified()
            return True
        except RPCError as e:
            logger.warning(
                f"Error de acceso a snapshot en Telegram ({job.chat.id}, msg {remote_snap.message_id}): {e}"
            )
            with db_transaction(self.db):
                remote_snap.mark_orphaned()
            return False

    def _validate_jit_batch(
        self, remotes: List[RemotePayload]
    ) -> tuple[List[RemotePayload], List[RemotePayload]]:
        """
        Verifica en Telegram si los mensajes de las piezas siguen vivos y no fueron borrados.
        Retorna (valid_remotes, orphaned_remotes).
        """
        if not remotes:
            return [], []

        chat_id = remotes[0].chat_id
        msg_ids = [r.message_id for r in remotes]

        valid_remotes: List[RemotePayload] = []
        orphaned_remotes: List[RemotePayload] = []

        from pyrogram.types import Message

        with db_transaction(self.db):
            for batch_ids in batched(msg_ids, 200):
                messages = cast(
                    Message | List[Message],
                    self.client.get_messages(chat_id, batch_ids),
                )
                if isinstance(messages, Message):
                    messages = [messages]

                msg_map = {
                    m.id: m
                    for m in messages
                    if m and getattr(m, "empty", False) is not True
                }

                for r in remotes:
                    if r.message_id not in batch_ids:
                        continue

                    msg = msg_map.get(r.message_id)

                    if (
                        not msg
                        or not msg.document
                        or msg.document.file_size != r.payload.size
                    ):
                        r.mark_orphaned()
                        orphaned_remotes.append(r)
                        logger.warning(
                            f"Mensaje {r.message_id} huérfano o alterado en Telegram. Marcado como huérfano."
                        )
                    else:
                        r.mark_verified(msg)
                        valid_remotes.append(r)

                time.sleep(0.3)

        return valid_remotes, orphaned_remotes

    def _search_potential_duplicates(self, job: Job) -> List[DuplicateCandidate]:
        """Busca heurísticamente documentos con nombres similares en el chat de destino."""
        if not job.source.is_folder and job.source.size < HEURISTIC_DUPLICATE_MIN_SIZE:
            return []

        raw_name = job.path.name if job.source.is_folder else job.path.stem
        clean_name = re.sub(r"[^\w\s]", " ", raw_name).strip()
        words = [w for w in clean_name.split() if len(w) > 1]
        if not words:
            return []

        search_query = " ".join(words[:4])
        if len(search_query) < 3:
            return []

        try:
            from pyrogram.types import Message

            found_messages = cast(
                List[Message],
                list(
                    self.client.search_messages(
                        chat_id=job.chat.id,
                        query=search_query,
                        filter=enums.MessagesFilter.DOCUMENT,
                        limit=5,
                    )
                ),
            )
        except Exception:
            return []

        duplicates: List[DuplicateCandidate] = []
        for msg in found_messages:
            if not msg.document:
                continue

            caption = msg.caption or ""
            if "#totelegram" in caption or "#src_" in caption:
                continue

            duplicates.append(
                DuplicateCandidate(
                    message_id=msg.id,
                    file_name=msg.document.file_name or "Desconocido",
                    file_size=msg.document.file_size or 0,
                    date=msg.date,
                )
            )

        return duplicates

    def _get_expected_count(self, job: Job) -> int:
        db_count = job.payloads.count()
        if db_count > 0:
            return db_count
        return job.total_expected_parts
