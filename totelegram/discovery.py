import logging
import math
import re
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, cast

import peewee
from pyrogram import enums
from pyrogram.errors import RPCError

from totelegram.common.helpers import batched
from totelegram.database import db_transaction
from totelegram.models import Job, Payload, RemotePayload, TelegramUser
from totelegram.packaging.importer import (
    InspectionStatus,
    SnapshotImporter,
    SnapshotImportPlanner,
    SnapshotInspector,
)
from totelegram.schemas import AvailabilityState, JobStatus
from totelegram.types import AvailabilityReport, DuplicateCandidate

if TYPE_CHECKING:
    from pyrogram.client import Client

logger = logging.getLogger(__name__)

# Umbral mínimo para buscar duplicados por nombre (20 MB)
HEURISTIC_DUPLICATE_MIN_SIZE = 20 * 1024 * 1024


class DiscoveryService:
    def __init__(self, client: "Client", db: peewee.Database):
        self.client = client
        self.db = db

    def investigate(self, job: Job) -> AvailabilityReport:
        """Analiza la disponibilidad del Job y devuelve un reporte con los recursos encontrados."""
        logger.info(
            f"Investigando disponibilidad de MD5 {job.source.md5sum} en chat {job.chat.id}"
        )

        # Camino Local: ¿Ya está en la base de datos y fresco/válido?
        if self.is_fulfilled_local(job):
            return AvailabilityReport(state=AvailabilityState.FULFILLED)

        # Descubrimiento JIT en Telegram: ¿Existe en el chat aunque no esté en la DB local?
        if self._discover_and_hydrate_jit(job):
            logger.info(
                f"Recurso MD5 {job.source.md5sum} descubierto y asimilado JIT desde Telegram."
            )
            # Re-verificar tras hidratar la DB
            if self.is_fulfilled_local(job):
                return AvailabilityReport(state=AvailabilityState.FULFILLED)

        # Camino Espejo: ¿Existe en otro chat para hacer Smart Forward?
        historical_jobs = self.get_historical_jobs(job)
        if historical_jobs.exists():
            remotes = self.get_remotes(historical_jobs)
            if remotes:
                return AvailabilityReport(
                    state=AvailabilityState.CAN_FORWARD, remotes=remotes
                )

        # Búsqueda heurística de posibles duplicados por nombre en el chat
        potential_duplicates = self._search_potential_duplicates(job)

        return AvailabilityReport(
            state=AvailabilityState.NEEDS_UPLOAD,
            potential_duplicates=potential_duplicates,
        )

    def _search_potential_duplicates(self, job: Job) -> List[DuplicateCandidate]:
        """Busca documentos en el chat de destino con nombres similares al recurso actual."""
        # Evitar consultas de red para archivos pequeños
        if not job.source.is_folder and job.source.size < HEURISTIC_DUPLICATE_MIN_SIZE:
            return []

        raw_name = job.path.name if job.source.is_folder else job.path.stem
        # Limpiar separadores y caracteres especiales dejando palabras legibles
        clean_name = re.sub(r"[^\w\s]", " ", raw_name).strip()
        words = [w for w in clean_name.split() if len(w) > 1]
        if not words:
            return []

        # Usar las primeras 4 palabras representativas
        search_query = " ".join(words[:4])
        if len(search_query) < 3:
            return []

        logger.debug(
            f"Ejecutando búsqueda heurística por nombre en chat {job.chat.id}: '{search_query}'"
        )

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
                    )  # type: ignore
                ),
            )
        except Exception as e:
            logger.debug(f"Aviso en búsqueda heurística de duplicados: {e}")
            return []

        duplicates: List[DuplicateCandidate] = []
        for msg in found_messages:
            if not msg.document:
                continue

            caption = msg.caption or ""
            # Omitir snapshots, manifiestos o locks del sistema
            if "#totelegram" in caption or "#src_" in caption:
                continue

            doc_name = msg.document.file_name or "Desconocido"
            doc_size = msg.document.file_size or 0

            duplicates.append(
                DuplicateCandidate(
                    message_id=msg.id,
                    file_name=doc_name,
                    file_size=doc_size,
                    date=msg.date,
                )
            )

        return duplicates

    def _discover_and_hydrate_jit(self, job: Job) -> bool:
        """Busca el snapshot del recurso en Telegram. Si lo encuentra, lo descarga e hidrata la DB.

        Retorna True si encontró y procesó un snapshot con éxito.
        """
        from totelegram.telegram.captions import ManifestCaption

        target_short_hash = job.source.short_hash
        query = f"#src_{target_short_hash}"
        logger.debug(
            f"Buscando snapshot en Telegram JIT en chat {job.chat.id} con query '{query}'"
        )

        try:
            from pyrogram.types import Message

            # Búsqueda nativa indexada por documento y hashtag
            found_messages = cast(
                List[Message],
                list(
                    self.client.search_messages(
                        chat_id=job.chat.id,
                        query=query,
                        filter=enums.MessagesFilter.DOCUMENT,
                        limit=5,
                    )  # type: ignore
                ),
            )

            # Filtrar validando con el contrato formal ManifestCaption
            manifest_msg = None
            for m in found_messages:
                if not m.document or not m.caption:
                    continue
                parsed = ManifestCaption.parse(m.caption)
                if parsed and parsed.source_short_hash == target_short_hash:
                    manifest_msg = m
                    break

            if not manifest_msg:
                logger.debug(
                    f"No se encontró mensaje de snapshot en Telegram para {query}"
                )
                return False

            logger.info(
                f"Snapshot detectado en Telegram (Msg ID: {manifest_msg.id}). Descargando para hidratar DB..."
            )

            # Descargar a directorio temporal aislado
            with tempfile.TemporaryDirectory() as temp_dir:
                ext = (
                    ".ttape"
                    if manifest_msg.document.file_name.endswith(".ttape")
                    else ".json.xz"
                )
                temp_snapshot_path = (
                    Path(temp_dir) / f"snapshot_{target_short_hash}{ext}"
                )

                downloaded = self.client.download_media(
                    message=manifest_msg,
                    file_name=str(temp_snapshot_path),
                )

                if not downloaded or not temp_snapshot_path.exists():
                    logger.warning(
                        "Fallo al descargar el archivo snapshot de Telegram durante JIT."
                    )
                    return False

                # Inspeccionar y Planificar la importación
                inspection = SnapshotInspector.inspect_file(temp_snapshot_path)
                if (
                    inspection.status != InspectionStatus.VALID
                    or not inspection.manifest
                ):
                    logger.warning(
                        f"Snapshot en Telegram corrupto o no válido ({inspection.error_detail})."
                    )
                    return False

                plan = SnapshotImportPlanner.plan([inspection])

                # Resolver propietario actual para vincular remotos
                me = self.client.get_me()
                owner = TelegramUser.get_or_create_from_tg(me) if me else None

                # Aplicar mutaciones en la DB persistiendo el snapshot_message_id
                succeeded, errors = SnapshotImporter.apply_plan(
                    plan=plan,
                    db=self.db,
                    owner=owner,
                    snapshot_message_id=manifest_msg.id,
                )

                if succeeded > 0:
                    logger.info(
                        f"Hidratación JIT completada con éxito para Job {job.id} "
                        f"(Snapshot Msg ID: {manifest_msg.id})"
                    )
                    return True
                else:
                    logger.warning(
                        f"Errores aplicando plan de importación JIT: {errors}"
                    )
                    return False

        except RPCError as e:
            logger.warning(f"Error de Telegram durante búsqueda JIT: {e}")
            return False
        except Exception:
            logger.exception("Excepción inesperada en búsqueda JIT de snapshot")
            return False

    def get_historical_jobs(self, job: Job) -> peewee.ModelSelect:
        return (
            Job.select()
            .where(
                (Job.source == job.source)
                & (Job.status == JobStatus.UPLOADED)
                & (Job.chat != job.chat)
                & (Job.deleted_at == 0)
            )
            .order_by(Job.updated_at.desc())  # type: ignore
        )

    def _validate_job_snapshot_jit(self, job: Job) -> bool:
        """Comprueba mediante una sola llamada si el mensaje del snapshot sigue existiendo en Telegram.

        Si el mensaje fue eliminado o el chat ya no es accesible, auto-sana el Job marcándolo como DELETED.
        """
        if not job.snapshot_message_id:
            return False

        try:
            from pyrogram.types import Message

            msg = cast(
                Message | None,
                self.client.get_messages(job.chat.id, job.snapshot_message_id),
            )

            # Si el mensaje no existe o está vacío en Telegram
            if msg is None or getattr(msg, "empty", True) or not msg.document:
                logger.warning(
                    f"Snapshot (Msg {job.snapshot_message_id}) no encontrado en chat {job.chat.id}. "
                    f"Auto-sanando Job {job.id} a DELETED."
                )
                with db_transaction(self.db):
                    job.mark_deleted()
                return False

            with db_transaction(self.db):
                job.mark_verified()
            return True

        except RPCError as e:
            logger.warning(
                f"Error de acceso en Telegram para Snapshot (Chat: {job.chat.id}, "
                f"Msg: {job.snapshot_message_id}): {e}. Auto-sanando Job {job.id} a DELETED."
            )
            with db_transaction(self.db):
                job.mark_deleted()
            return False

    def is_fulfilled_local(self, job: Job) -> bool:
        """Verifica si el Job actual ya está completo en el destino.

        Aplica fast-path de frescura (TTL 30m) y validación ligera del snapshot.
        """
        # Si el Job ya fue marcado como subido y tiene snapshot registrado
        if (
            job.status == JobStatus.UPLOADED
            and job.snapshot_message_id
            and job.deleted_at == 0
        ):
            # Camino ultrarrápido O(1): Si está fresco (< 30 min), es válido sin tocar la red
            if job.is_fresh:
                logger.debug(
                    f"Job {job.id} está fresco (verificado recientemente). Disponibilidad confirmada en local."
                )
                return True

            # Si expiró la frescura (> 30 min), comprobamos el snapshot en Telegram (50ms)
            logger.debug(
                f"Job {job.id} expiró frescura TTL. Verificando snapshot en Telegram..."
            )
            # Si el snapshot falló o fue borrado, _validate_job_snapshot_jit ya lo marcó como DELETED
            return bool(self._validate_job_snapshot_jit(job))

        # Fallback para Jobs en curso o sin snapshot publicado: validación por lotes de chunks
        local_remotes = list(
            RemotePayload.select()
            .join(Payload)
            .where((Payload.job == job) & (RemotePayload.is_orphaned == False))
            .order_by(Payload.sequence_index)
        )

        expected = self._get_expected_count(job)
        if len(local_remotes) == expected and expected > 0:
            return self._validate_jit_batch(local_remotes)
        return False

    def get_remotes(self, historical_jobs):
        for hist_job in historical_jobs:
            # Si el Job histórico tiene snapshot, usamos la validación rápida del snapshot
            if hist_job.snapshot_message_id:
                if not hist_job.is_fresh and not self._validate_job_snapshot_jit(
                    hist_job
                ):
                    continue

                remotes = list(
                    RemotePayload.select(RemotePayload, Payload)
                    .join(Payload)
                    .where(
                        (Payload.job == hist_job) & (RemotePayload.is_orphaned == False)
                    )
                    .order_by(Payload.sequence_index)
                )
                if len(remotes) == self._get_expected_count(hist_job):
                    logger.info(
                        f"Espejo íntegro confirmado vía snapshot: Job {hist_job.id}"
                    )
                    return remotes
                continue

            # Fallback para jobs históricos antiguos sin snapshot registrado
            remotes = list(
                RemotePayload.select(RemotePayload, Payload)
                .join(Payload)
                .where((Payload.job == hist_job) & (RemotePayload.is_orphaned == False))
                .order_by(Payload.sequence_index)
            )

            expected_count = self._get_expected_count(hist_job)

            if len(remotes) == expected_count and self._validate_jit_batch(remotes):
                logger.info(f"Espejo íntegro: Job {hist_job.id}")
                return remotes
        return None

    def _validate_jit_batch(self, remotes: List[RemotePayload]) -> bool:
        """Verifica en Telegram si los mensajes de un Job siguen existiendo.
        Usa caché interna para evitar peticiones redundantes.
        """
        logger.debug(
            f"Iniciando validación JIT en Telegram para {len(remotes)} mensajes"
        )
        if not remotes:
            return False

        to_verify = [r for r in remotes if not r.is_fresh]

        if not to_verify:
            return True

        chat_id = to_verify[0].chat_id
        msg_ids = [r.message_id for r in to_verify]
        is_integral = True

        from pyrogram.types import Message

        with db_transaction(self.db):
            for batch_ids in batched(msg_ids, 200):
                messages = cast(
                    Message | List[Message],
                    self.client.get_messages(chat_id, batch_ids),
                )
                if isinstance(messages, Message):
                    messages = [messages]

                for msg in messages:
                    remote = next(
                        (r for r in to_verify if r.message_id == msg.id), None
                    )
                    if not remote:
                        continue

                    # Si un solo mensaje del set falló, el espejo no es íntegro
                    if msg is None or getattr(msg, "empty", True) or not msg.document:
                        remote.mark_orphaned()
                        is_integral = False
                        logger.warning(
                            f"Mensaje {remote.message_id} no encontrado o vacío en Telegram. Marcando como huérfano."
                        )
                        continue

                    # Verificación extra: ¿El tamaño coincide? (Anti-edición)
                    if msg.document.file_size != remote.payload.size:
                        remote.mark_orphaned()
                        is_integral = False
                    else:
                        remote.mark_verified(msg)

                time.sleep(0.5)
            return is_integral

    def _get_expected_count(self, job: Job) -> int:
        db_count = job.payloads.count()
        if db_count > 0:
            return db_count
        return max(1, math.ceil(job.source.size / job.config.tg_max_size))
