import json
import logging
import math
from datetime import datetime, timezone
from itertools import batched
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Generator, List, Optional, Tuple, cast

import peewee
import tartape
from tartape import calculate_file_slices
from tartape.schemas import FileSlice

from totelegram.common.helpers import is_expired as helpers_is_expired
from totelegram.database import db_transaction
from totelegram.packaging.partitioner import StatelessPartitioner
from totelegram.packaging.schemas import VirtualChunk
from totelegram.schemas import ResourceType, SourceType
from totelegram.telegram.client import parse_message_json_data

if TYPE_CHECKING:
    from pyrogram.types import Chat as TgChat
    from pyrogram.types import Message


from totelegram.common.files import create_md5sum_by_hashlib, get_mimetype
from totelegram.common.helpers import get_utc_now
from totelegram.database import EnumField, PydanticJSONField, db_proxy
from totelegram.schemas import TapeCatalog

logger = logging.getLogger(__name__)


class PortableJSONField(peewee.TextField):
    """
    Campo JSON que persiste como texto estándar en SQLite.
    Serializa y deserializa automáticamente objetos y listas Python.
    """

    def db_value(self, value):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    def python_value(self, value):
        if value is None:
            return None
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value


class BaseModel(peewee.Model):
    created_at = peewee.DateTimeField(default=get_utc_now)
    updated_at = peewee.DateTimeField(default=get_utc_now)

    def save(self, *args, **kwargs):
        self.updated_at = get_utc_now()
        return super().save(*args, **kwargs)

    class Meta:
        database = db_proxy


class TelegramChat(BaseModel):
    id = cast(int, peewee.BigIntegerField(primary_key=True))
    title = cast(str, peewee.CharField(null=True))
    username = cast(str | None, peewee.CharField(null=True))
    type = cast(str, peewee.CharField())
    is_public = cast(bool, peewee.BooleanField(default=False))
    last_verified = cast(datetime | None, peewee.DateTimeField(null=True))

    @staticmethod
    def get_or_create_from_chat(tg_chat: "TgChat") -> Tuple["TelegramChat", bool]:
        defaults = {
            "title": tg_chat.title,
            "username": tg_chat.username,
            "type": str(tg_chat.type.value),
            "is_public": bool(tg_chat.username),
            "last_verified": datetime.now(timezone.utc),
        }
        try:
            with db_proxy.atomic():
                return TelegramChat.get_or_create(id=tg_chat.id, defaults=defaults)
        except peewee.IntegrityError:
            # Rescate concurrente si otro nodo lo creó en el mismo milisegundo
            return TelegramChat.get_by_id(tg_chat.id), False

    def update_from_tg(self, tg_chat: "TgChat"):
        self.title = tg_chat.title
        self.username = tg_chat.username
        self.type = str(tg_chat.type.value)
        self.is_public = bool(tg_chat.username)
        self.last_verified = datetime.now(timezone.utc)
        self.save(
            only=[
                TelegramChat.title,
                TelegramChat.username,
                TelegramChat.type,
                TelegramChat.is_public,
                TelegramChat.last_verified,
                TelegramChat.updated_at,
            ]
        )


class TelegramUser(BaseModel):
    id = cast(int, peewee.BigIntegerField(primary_key=True))
    first_name = cast(str, peewee.CharField())
    username = cast(str | None, peewee.CharField(null=True))
    is_premium = cast(bool, peewee.BooleanField(default=False))
    last_seen = peewee.DateTimeField(default=lambda: datetime.now(timezone.utc))

    @staticmethod
    def get_or_create_from_tg(tg_user) -> "TelegramUser":
        defaults = {
            "first_name": tg_user.first_name,
            "username": tg_user.username,
            "is_premium": tg_user.is_premium or False,
            "last_seen": datetime.now(timezone.utc),
        }
        try:
            with db_proxy.atomic():
                user, created = TelegramUser.get_or_create(
                    id=tg_user.id, defaults=defaults
                )
        except peewee.IntegrityError:
            user = TelegramUser.get_by_id(tg_user.id)
            created = False

        if not created:
            user.first_name = tg_user.first_name
            user.username = tg_user.username
            user.is_premium = tg_user.is_premium or False
            user.last_seen = datetime.now(timezone.utc)
            user.save(
                only=[
                    TelegramUser.first_name,
                    TelegramUser.username,
                    TelegramUser.is_premium,
                    TelegramUser.last_seen,
                    TelegramUser.updated_at,
                ]
            )
        return user


class Source(BaseModel):
    """Representa la identidad única de un recurso (Archivo o Carpeta)."""

    id: int
    members: list

    path_str = cast(str, peewee.CharField())
    md5sum = cast(
        str, peewee.CharField(unique=True)
    )  # MD5 o Fingerprint # FIXME: Nombre de campo incoherente
    size = cast(int, peewee.BigIntegerField())

    # mtime : Unix timestamp. Es util para la identificar archivo
    mtime = cast(float, peewee.DoubleField())
    mimetype = cast(str, peewee.CharField())
    type = cast(SourceType, EnumField(SourceType, default=SourceType.FILE))  # type: ignore

    tape_catalog = cast(TapeCatalog | None, PydanticJSONField(TapeCatalog, null=True))

    @property
    def short_hash(self) -> str:
        """
        Retorna el identificador normalizado de 32 caracteres (128 bits).
        Para archivos individuales es su MD5 completo.
        Para carpetas son los primeros 32 caracteres de su fingerprint estructural.
        Se utiliza como baliza universal en Telegram (#src_<short_hash>).
        """
        return self.md5sum[:32]

    @property
    def path(self) -> Path:
        return Path(self.path_str)

    @property
    def is_folder(self) -> bool:
        return self.type == SourceType.FOLDER

    @staticmethod
    def _is_mtime_matching(cached_mtime: float, current_mtime: float) -> bool:
        diff = abs(cached_mtime - current_mtime)

        # Coincidencia para sistemas modernos (Linux, Windows, macOS):
        # Tolera únicamente infinitesimales de float64 (< 1 milisegundo)
        if diff < 0.001:
            return True

        # Tolerancia para sistemas sin subsegundos (FAT32, TAR, ZIP):
        # Solo si alguno de los dos timestamps no tiene parte fraccionaria (redondeado a segundo entero)
        is_cached_integer = (cached_mtime % 1.0) == 0.0
        is_current_integer = (current_mtime % 1.0) == 0.0

        return bool((is_cached_integer or is_current_integer) and diff <= 2.0)

    @staticmethod
    def get_or_create_from_filepath(
        path: Path,
        on_hash_start: Callable[[str, int], None] | None = None,
        on_hash_progress: Callable[[int, int], None] | None = None,
        on_hash_complete: Callable[[str, str], None] | None = None,
    ) -> "Source":
        stat = path.stat()
        current_size = stat.st_size
        current_mtime = stat.st_mtime
        canonical_path = str(path.resolve())

        # Intenta resolver por caché rápido
        cached = Source.get_or_none(Source.path_str == canonical_path)
        if cached:
            size_matches = cached.size == current_size
            mtime_matches = Source._is_mtime_matching(cached.mtime, current_mtime)

            if size_matches and mtime_matches:
                return cached

        # Si no coincide estrictamente, se procede al hash completo seguro...
        if on_hash_start:
            on_hash_start(path.name, current_size)

        md5sum = create_md5sum_by_hashlib(path, on_progress=on_hash_progress)

        if on_hash_complete:
            on_hash_complete(path.name, md5sum)

        # Comprobar si existe el MD5
        source = cast(Source | None, Source.get_or_none(Source.md5sum == md5sum))
        if source:
            source.update_if_needed(path)
            return source

        try:
            with db_proxy.atomic():
                return Source.create(
                    md5sum=md5sum,
                    path_str=canonical_path,
                    size=current_size,
                    mtime=current_mtime,
                    mimetype=get_mimetype(path),
                )
        except peewee.IntegrityError:
            source = cast(Source, Source.get(Source.md5sum == md5sum))
            source.update_if_needed(path)
            return source

    @classmethod
    def get_or_create_from_tape(cls, tape: tartape.Tape) -> "Source":
        try:
            source = Source.get(Source.md5sum == tape.fingerprint)
            tape.verify(raise_exception=True)
            return source
        except peewee.DoesNotExist:
            logger.info(f"Re-indexando cinta encontrada en: {tape.directory}")
            try:
                with db_proxy.atomic():
                    return cls.create_from_tape(tape, tape.exclude_patterns)
            except peewee.IntegrityError:
                return Source.get(Source.md5sum == tape.fingerprint)

    @classmethod
    def create_from_tape(
        cls, tape: tartape.Tape, exclusion_patterns: List[str] | str
    ) -> "Source":
        exclude_patterns = (
            json.dumps(exclusion_patterns)
            if isinstance(exclusion_patterns, list)
            else exclusion_patterns
        )

        catalog = TapeCatalog(
            fingerprint=tape.fingerprint,
            total_size=tape.total_size,
            total_files=tape.count_files,
            tartape_version=tartape.__version__,
            created_at=tape.created_at,
            exclude_patterns=exclude_patterns,
        )

        source = Source.create(
            path_str=str(tape.directory),
            md5sum=catalog.fingerprint,
            size=catalog.total_size,
            mtime=tape.created_at,
            mimetype="application/x-tar",
            tape_catalog=catalog,
            type=SourceType.FOLDER,
        )
        return source

    def update_if_needed(self, path: Path) -> bool:
        if self.is_folder:
            return False

        stat = path.stat()
        current_size = stat.st_size
        current_mtime = stat.st_mtime
        canonical_path = str(path.resolve())

        changed = False
        if self.path_str != canonical_path:
            self.path_str = canonical_path
            changed = True
        if abs(self.mtime - current_mtime) >= 1.0:
            self.mtime = current_mtime
            changed = True
        if self.size != current_size:
            self.size = current_size
            changed = True

        if changed:
            self.save(
                only=[
                    Source.path_str,
                    Source.size,
                    Source.mtime,
                    Source.updated_at,
                ]
            )
        return changed

    def index_tape_members(self, tape: tartape.Tape) -> None:
        """Indexa los archivos de la cinta TAR directamente en TapeMember (ADR-009)."""
        if not self.is_folder:
            return

        if self.members.count() > 0:  # type: ignore
            return

        try:
            assert tape.catalog_path is not None
            with tartape.open_catalog(tape.catalog_path, read_only=True) as cat:
                TapeMember.bulk_insert_from_catalog(self, cat)
        except Exception as e:
            logger.warning(
                f"Aviso al indexar miembros de cinta para '{self.path_str}': {e}"
            )


class Job(BaseModel):
    chat_id: int
    id: int
    payloads: peewee.ModelSelect

    source = cast(Source, peewee.ForeignKeyField(Source, backref="jobs"))
    chat = peewee.ForeignKeyField(TelegramChat, backref="jobs")
    chunk_size = cast(int, peewee.BigIntegerField())

    last_verified_at = cast(datetime | None, peewee.DateTimeField(null=True))

    @property
    def is_chunked(self) -> bool:
        """Determina si el recurso físico excede el tamaño de bloque y fue dividido."""
        return self.source.size > self.chunk_size

    @property
    def total_expected_parts(self) -> int:
        """Calcula de forma determinista la cantidad de piezas esperadas para este recurso."""
        return max(1, math.ceil(self.source.size / self.chunk_size))

    @property
    def is_completed_local(self) -> bool:
        """
        Determina si todas las piezas físicas del Job están registradas en la base de datos local.
        Es una verdad matemática pura derivada de Payload y RemotePayload (Cero Split-Brain).
        """
        total = self.payloads.count()
        if total == 0:
            return False
        return Payload.total_pending_for_job(self) == 0

    @property
    def has_remote_snapshot(self) -> bool:
        """Indica si existe un snapshot remoto activo (no huérfano) en Telegram."""
        return self.remote_snapshot is not None

    @property
    def path(self) -> Path:
        return Path(self.source.path_str)

    @property
    def remote_snapshot(self) -> Optional["RemoteSnapshot"]:
        """Retorna el snapshot remoto activo (no huérfano) asociado a este Job."""
        return RemoteSnapshot.get_or_none(
            (RemoteSnapshot.job == self) & (RemoteSnapshot.is_orphaned == False)
        )

    @property
    def snapshot_message_id(self) -> int | None:
        """Propiedad de conveniencia: ID del mensaje en Telegram del snapshot activo."""
        snap = self.remote_snapshot
        if snap:
            return snap.message_id
        return getattr(self, "_temp_snapshot_message_id", None)

    @snapshot_message_id.setter
    def snapshot_message_id(self, value: int | None):
        """Setter de compatibilidad para evitar errores durante Peewee.create."""
        self._temp_snapshot_message_id = value
        if (
            value is not None
            and getattr(self, "id", None)
            and getattr(self, "chat", None)
        ):
            owner = TelegramUser.select().first() or TelegramUser.create(
                id=0, first_name="System"
            )
            RemoteSnapshot.register(self, value, self.chat, owner)

    def set_snapshot_published(
        self, message_id: int, owner: Optional["TelegramUser"] = None
    ) -> "RemoteSnapshot":
        """Registra el RemoteSnapshot en Telegram y actualiza la fecha de verificación."""
        if owner is None:
            first_remote = (
                RemotePayload.select().join(Payload).where(Payload.job == self).first()
            )
            owner = (
                first_remote.owner if first_remote else TelegramUser.select().first()
            )

        if owner is None:
            owner, _ = TelegramUser.get_or_create(
                id=0, defaults={"first_name": "System"}
            )

        remote = RemoteSnapshot.register(
            job=self,
            message_id=message_id,
            chat=self.chat,
            owner=owner,  # type: ignore
        )
        self.last_verified_at = get_utc_now()
        self.save(only=[Job.last_verified_at, Job.updated_at])
        return remote

    @staticmethod
    def formalize_intent(
        source: "Source",
        chat: "TelegramChat",
        chunk_size: int,
    ) -> "Job":
        """Formaliza la intención inmutable de proyectar un Source en un TelegramChat."""
        with db_proxy.atomic():
            # Si ya existe un intento incompleto previo con el mismo corte, lo reutilizamos
            incomplete = Job.get_incomplete_for_source_in_chat(source, chat)
            if incomplete and incomplete.chunk_size == chunk_size:
                return incomplete

            return Job.create(
                source=source,
                chat=chat,
                chunk_size=chunk_size,
            )

    @staticmethod
    def get_incomplete_for_source_in_chat(
        source: "Source", chat: "TelegramChat"
    ) -> Optional["Job"]:
        """Busca si existe un Job incompleto previo para este Source en este chat."""
        query = (
            Job.select()
            .where((Job.source == source) & (Job.chat == chat))
            .order_by(Job.created_at.desc())
        )
        for job in query:
            if not job.is_completed_local:
                return job
        return None

    @staticmethod
    def get_completed_for_source_in_chat(
        source: "Source", chat: "TelegramChat"
    ) -> List["Job"]:
        """Retorna todos los Jobs completados para este Source en este chat (historial legítimo)."""
        query = (
            Job.select()
            .where((Job.source == source) & (Job.chat == chat))
            .order_by(Job.created_at.desc())
        )
        return [j for j in query if j.is_completed_local]

    @staticmethod
    def get_for_source_in_chat(
        source: "Source", chat: "TelegramChat"
    ) -> Optional["Job"]:
        """Devuelve el Job activo relevante. Prioriza el incompleto; si no hay, devuelve el completado más reciente."""
        incomplete = Job.get_incomplete_for_source_in_chat(source, chat)
        if incomplete:
            return incomplete
        completed = Job.get_completed_for_source_in_chat(source, chat)
        return completed[0] if completed else None

    def purge(self, db: peewee.Database | None = None) -> None:
        """
        Elimina físicamente el Job y todos sus registros dependientes en SQLite.
        Garantiza limpieza atómica sin violar restricciones de clave foránea.
        """
        target_db = db or self._meta.database
        with db_transaction(target_db):
            # Eliminar RemotePayloads asociados
            RemotePayload.delete().where(
                RemotePayload.payload << self.payloads
            ).execute()

            # Eliminar RemoteSnapshots asociados
            RemoteSnapshot.delete().where(RemoteSnapshot.job == self).execute()

            # Eliminar Payloads
            Payload.delete().where(Payload.job == self).execute()

            # Eliminar el Job
            self.delete_instance()
            logger.debug(f"Job {self.id} purgado físicamente de la base de datos.")

    def prepare_chunks(self, path: Path, settings=None) -> List["Payload"]:
        """Genera y persiste las piezas lógicas según el tamaño de corte del contrato."""
        if self.payloads.count() > 0:
            return cast(
                List["Payload"], list(self.payloads.order_by(Payload.sequence_index))
            )

        if self.source.type == SourceType.FOLDER:
            chunks = StatelessPartitioner.partition_folder(path, self.chunk_size)
            return self._persist_chunks(chunks)
        else:
            chunks = StatelessPartitioner.partition_file(
                path, self.chunk_size, self.source.md5sum
            )
            return self._persist_chunks(chunks)

    def _persist_chunks(self, virtual_chunks: List[VirtualChunk]) -> List["Payload"]:
        payloads_data = [
            {
                "job": self,
                "sequence_index": vc.sequence_index,
                "start_offset": vc.start_offset,
                "end_offset": vc.end_offset,
                "size": vc.size,
                "filename": vc.filename,
                "filename_short": vc.filename_short,
                "md5sum": vc.md5sum,
            }
            for vc in virtual_chunks
        ]

        with db_transaction(self._meta.database):
            Payload.insert_many(payloads_data).execute()

        return cast(List[Payload], list(self.payloads.order_by(Payload.sequence_index)))

    def is_fresh(self) -> bool:
        """Determina si la verificación JIT del Job aún es confiable."""
        if not self.last_verified_at:
            return False
        return not helpers_is_expired(self.last_verified_at, grace_period_seconds=1800)

    def mark_verified(self):
        """Actualiza la fecha de verificación JIT del Job."""
        self.last_verified_at = get_utc_now()
        self.save(only=[Job.last_verified_at, Job.updated_at])


# Índice compuesto único para un Job activo por chat
# TODO: simplificar, la eliminacion es total en la DB.
# Job.add_index(Job.source, Job.chat, Job.deleted_at, unique=True)


class Payload(BaseModel):
    """Representa una parte física (trozo) que compone un Job."""

    id: int
    payloads: Generator["Payload", None, None]
    job_id: int

    job = cast(
        Job, peewee.ForeignKeyField(Job, backref="payloads", on_delete="CASCADE")
    )
    md5sum = cast(str | None, peewee.CharField(null=True))

    filename = cast(str, peewee.CharField())
    filename_short = cast(str, peewee.CharField())
    sequence_index = cast(int, peewee.IntegerField())
    start_offset = cast(int, peewee.BigIntegerField())
    end_offset = cast(int, peewee.BigIntegerField())
    size = cast(int, peewee.BigIntegerField())

    @property
    def has_remote(self) -> bool:
        return (
            RemotePayload.select()
            .where(
                (RemotePayload.payload == self) & (RemotePayload.is_orphaned == False)
            )
            .exists()
        )

    @staticmethod
    def total_pending_for_job(job: "Job") -> int:
        """Cuenta las piezas que aún no tienen un RemotePayload válido."""
        valid_remotes = RemotePayload.select().where(
            (RemotePayload.payload == Payload.id) & (RemotePayload.is_orphaned == False)
        )

        return (
            Payload.select()
            .where((Payload.job == job) & (~peewee.fn.EXISTS(valid_remotes)))
            .count()
        )


class RemotePayload(BaseModel):
    id: int
    payload_id: int
    chat_id: int

    payload = peewee.ForeignKeyField(Payload, backref="remotes", on_delete="CASCADE")
    message_id = cast(int, peewee.IntegerField())
    chat = peewee.ForeignKeyField(TelegramChat, backref="remote_contents")
    owner = cast(
        TelegramUser, peewee.ForeignKeyField(TelegramUser, backref="remote_contents")
    )
    json_metadata = cast(dict, PortableJSONField())

    last_verified_at = cast(datetime | None, peewee.DateTimeField(null=True))
    is_orphaned = cast(bool, peewee.BooleanField(default=False))

    def mark_orphaned(self):
        self.is_orphaned = True
        self.save(only=[RemotePayload.is_orphaned, RemotePayload.updated_at])

    def mark_verified(self, message: "Message"):
        # Solo orfana si el mensaje es None o si Pyrogram explícitamente marcó empty como True
        if message is None or getattr(message, "empty", False) is True:
            return self.mark_orphaned()

        self.last_verified_at = datetime.now(timezone.utc)
        self.is_orphaned = False
        self.json_metadata = json.loads(str(message))
        self.save(
            only=[
                RemotePayload.last_verified_at,
                RemotePayload.is_orphaned,
                RemotePayload.json_metadata,
                RemotePayload.updated_at,
            ]
        )

    @property
    def sequence_index(self) -> int:
        return self.payload.sequence_index

    @property
    def is_fresh(self) -> bool:
        """Determina si la validación aún es confiable (15 minutos)."""
        if self.is_orphaned or not self.last_verified_at:
            return False

        # Si last_verified_at + 900 segundos ya expiró, entonces no está "fresh"
        return not helpers_is_expired(self.last_verified_at, grace_period_seconds=900)

    @staticmethod
    def register_upload(
        payload: Payload, tg_message, owner: TelegramUser
    ) -> "RemotePayload":
        """Registra una subida exitosa con trazabilidad de cuenta."""
        return RemotePayload.create(
            payload=payload,
            message_id=tg_message.id,
            chat_id=tg_message.chat.id,
            owner=owner,
            json_metadata=json.loads(str(tg_message)),
        )

    @property
    def message(self) -> "Message":
        return parse_message_json_data(self.json_metadata)


class RemoteSnapshot(BaseModel):
    """Representa el mensaje del Snapshot (.ttape / .json.xz) alojado en Telegram."""

    id: int
    job = cast(
        Job,
        peewee.ForeignKeyField(Job, backref="remote_snapshots", on_delete="CASCADE"),
    )
    chat = cast(
        TelegramChat, peewee.ForeignKeyField(TelegramChat, backref="remote_snapshots")
    )
    message_id = cast(int, peewee.BigIntegerField())
    owner = cast(
        TelegramUser, peewee.ForeignKeyField(TelegramUser, backref="remote_snapshots")
    )

    last_verified_at = cast(datetime | None, peewee.DateTimeField(null=True))
    is_orphaned = cast(bool, peewee.BooleanField(default=False))

    class Meta:  # type: ignore
        indexes = ((("job", "is_orphaned"), False),)

    @staticmethod
    def register(
        job: Job,
        message_id: int,
        chat: TelegramChat,
        owner: TelegramUser,
    ) -> "RemoteSnapshot":
        """Registra el enlace al mensaje del snapshot en Telegram."""
        return RemoteSnapshot.create(
            job=job,
            chat=chat,
            message_id=message_id,
            owner=owner,
            last_verified_at=get_utc_now(),
            is_orphaned=False,
        )

    def mark_orphaned(self):
        """Marca el snapshot como eliminado en Telegram."""
        self.is_orphaned = True
        self.save(only=[RemoteSnapshot.is_orphaned, RemoteSnapshot.updated_at])

    def mark_verified(self):
        """Actualiza la fecha de verificación JIT del snapshot."""
        self.last_verified_at = get_utc_now()
        self.is_orphaned = False
        self.save(
            only=[
                RemoteSnapshot.last_verified_at,
                RemoteSnapshot.is_orphaned,
                RemoteSnapshot.updated_at,
            ]
        )

    @property
    def is_fresh(self) -> bool:
        """Determina si la verificación aún es confiable (30 minutos)."""
        if self.is_orphaned or not self.last_verified_at:
            return False
        return not helpers_is_expired(self.last_verified_at, grace_period_seconds=1800)


class TapeMember(BaseModel):
    id: int
    source = cast("Source", peewee.ForeignKeyField(Source, backref="members"))
    relative_path = cast(str, peewee.CharField())
    size = cast(int, peewee.BigIntegerField())
    md5sum = cast(str, peewee.CharField())
    start_offset = cast(int, peewee.BigIntegerField(default=0))

    class Meta:  # type: ignore
        indexes = ((("source", "relative_path"), True),)

    def get_slices(self, volume_size: int) -> list[FileSlice]:
        """Calcula los fragmentos quirúrgicos del archivo usando las matemáticas de TarTape.

        Args:
            volume_size: Tamaño de partición de los volúmenes en bytes.

        Returns:
            list[FileSlice]: Lista de fragmentos calculados con aritmética O(1).
        """
        return calculate_file_slices(
            start_offset=self.start_offset,
            file_size=self.size,
            volume_size=volume_size,
        )

    @classmethod
    def bulk_insert_from_catalog(
        cls, source: "Source", catalog: tartape.Catalog, batch_size: int = 500
    ) -> int:
        """
        Extrae e inserta en lote las pistas (archivos no-directorio) desde un tartape.Catalog
        hacia la tabla TapeMember asociada a un Source.
        """
        with catalog.db_session:
            cursor = catalog.db_session.db.execute_sql(
                "SELECT arc_path, size, checksum, start_offset FROM track WHERE is_dir = 0"
            )
            tracks = cursor.fetchall()

        member_inserts = [
            {
                "source": source,
                "relative_path": row[0],
                "size": row[1],
                "md5sum": row[2] or "",
                "start_offset": row[3] or 0,
            }
            for row in tracks
        ]

        total_inserted = 0
        for batch in batched(member_inserts, batch_size):
            total_inserted += cls.insert_many(batch).on_conflict_ignore().execute()
        return total_inserted


class Claim(BaseModel):
    # 'account:123456789' o 'job:543'
    resource_id = peewee.CharField(primary_key=True)
    resource_type = EnumField(ResourceType)  # type: ignore
    node_id = cast(str, peewee.CharField())
    expires_at = cast(datetime, peewee.DateTimeField())

    @classmethod
    def is_expired(cls, claim: "Claim") -> bool:
        return helpers_is_expired(claim.expires_at)
