import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Generator, List, Optional, Tuple, cast

import peewee
import tartape
from tartape.schemas import EntryState, ManifestEntry

from totelegram import __VERSION__
from totelegram.database import db_transaction
from totelegram.packaging.partitioner import StatelessPartitioner
from totelegram.packaging.schemas import VirtualChunk, VirtualTapeMember
from totelegram.schemas import JobStatus, ResourceType, SourceType, Strategy
from totelegram.telegram.client import parse_message_json_data

if TYPE_CHECKING:
    from pyrogram.types import Chat as TgChat
    from pyrogram.types import Message


from totelegram.common.files import create_md5sum_by_hashlib, get_mimetype
from totelegram.common.helpers import batched, get_utc_now
from totelegram.database import EnumField, PydanticJSONField, db_proxy
from totelegram.schemas import StrategyConfig, TapeCatalog

logger = logging.getLogger(__name__)


class PortableJSONField(peewee.TextField):
    """
    Campo JSON que persiste como texto estándar en cualquier base de datos.
    Esto permite compatibilidad nativa entre SQLite y Postgres sin usar extensiones de drivers.
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
    username = cast(Optional[str], peewee.CharField(null=True))
    type = cast(str, peewee.CharField())
    is_public = cast(bool, peewee.BooleanField(default=False))
    last_verified = cast(Optional[datetime], peewee.DateTimeField(null=True))

    @staticmethod
    def get_or_create_from_chat(tg_chat: "TgChat") -> Tuple["TelegramChat", bool]:
        defaults = {
            "title": tg_chat.title,
            "username": tg_chat.username,
            "type": str(tg_chat.type.value),
            "is_public": True if tg_chat.username else False,
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
        self.is_public = True if tg_chat.username else False
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
    username = cast(Optional[str], peewee.CharField(null=True))
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

    path_str = cast(str, peewee.CharField())
    md5sum = cast(str, peewee.CharField(unique=True))  # MD5 o Fingerprint
    size = cast(int, peewee.BigIntegerField())
    # mtime : Unix timestamp. Es util para la identificar archivo
    mtime = cast(float, peewee.FloatField())
    mimetype = cast(str, peewee.CharField())
    type = cast(SourceType, EnumField(SourceType, default=SourceType.FILE))  # type: ignore

    tape_catalog = cast(
        Optional[TapeCatalog], PydanticJSONField(TapeCatalog, null=True)
    )

    @property
    def path(self) -> Path:
        return Path(self.path_str)

    @property
    def is_folder(self) -> bool:
        return self.type == SourceType.FOLDER

    @staticmethod
    def get_or_create_from_filepath(path: Path) -> "Source":
        stat = path.stat()
        current_size = stat.st_size
        current_mtime = stat.st_mtime
        path_str = str(path)

        cached = Source.get_or_none(
            (Source.path_str == path_str)
            & (Source.size == current_size)
            & (Source.mtime == current_mtime)
        )
        if cached:
            return cached

        md5sum = create_md5sum_by_hashlib(path)
        source = cast(Optional[Source], Source.get_or_none(Source.md5sum == md5sum))
        if source:
            source.update_if_needed(path)
            return source

        try:
            with db_proxy.atomic():
                return Source.create(
                    md5sum=md5sum,
                    path_str=path_str,
                    size=current_size,
                    mtime=current_mtime,
                    mimetype=get_mimetype(path),
                )
        except peewee.IntegrityError:
            # Rescate si otro proceso/nodo insertó el mismo MD5
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
            # TODO implementar para carpeta.
            return False

        stat = path.stat()
        current_size = stat.st_size
        current_mtime = stat.st_mtime
        path_str = str(path)

        changed = False
        if self.path_str != path_str:
            self.path_str = path_str
            changed = True
        if self.mtime != current_mtime:
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


class Job(BaseModel):
    id: int
    payloads: peewee.ModelSelect

    source = cast(Source, peewee.ForeignKeyField(Source, backref="jobs"))
    chat = peewee.ForeignKeyField(TelegramChat, backref="jobs")

    strategy = cast(Strategy, EnumField(Strategy))  # type: ignore
    config = cast(StrategyConfig, PydanticJSONField(StrategyConfig))
    status = cast(JobStatus, EnumField(JobStatus))  # type: ignore

    deleted_at = cast(float, peewee.FloatField(default=0))

    @property
    def path(self) -> Path:
        return Path(self.source.path_str)

    def set_uploaded(self):
        self.status = JobStatus.UPLOADED
        self.save(only=[Job.status, Job.updated_at])

    @staticmethod
    def formalize_intent(
        source: "Source",
        chat: "TelegramChat",
        is_premium: bool,
        tg_limit: int,
    ) -> "Job":
        """
        Crea un Job basado en la estrategía y la configuración de la cuenta.
        """

        strategy = Strategy.evaluate(source.size, tg_limit)
        config = StrategyConfig(
            tg_max_size=tg_limit, user_is_premium=is_premium, app_version=__VERSION__
        )
        try:
            with db_proxy.atomic():
                return Job.create(
                    source=source,
                    chat=chat,
                    strategy=strategy,
                    status=JobStatus.PENDING,
                    config=config,
                )
        except peewee.IntegrityError:
            existing = Job.get_for_source_in_chat(source, chat)
            if existing:
                return existing
            raise

    @staticmethod
    def get_for_source_in_chat(
        source: "Source", chat: "TelegramChat"
    ) -> Optional["Job"]:
        """Devuelve el Job que existe para el source en el chat especificado."""
        return Job.get_or_none(
            (Job.source == source) & (Job.chat == chat) & (Job.deleted_at == 0)
        )

    def adopt_job(self, job: "Job") -> "Job":
        self.strategy = job.strategy
        self.status = job.status
        self.config = job.config
        self.save(only=[Job.strategy, Job.status, Job.config, Job.updated_at])
        return self

    def mark_deleted(self):

        query = RemotePayload.update(is_orphaned=True).where(
            RemotePayload.payload << self.payloads
        )
        query.execute()

        self.deleted_at = time.time()
        self.status = JobStatus.DELETED
        self.save(only=[Job.deleted_at, Job.status, Job.updated_at])

        logger.debug(f"Job {self.id} invalidado y remotos orfanados.")

    def prepare_chunks(self, path: Path, settings) -> List["Payload"]:
        """
        Orquesta el particionado físico delegando en StatelessPartitioner
        y persiste los resultados en la base de datos de manera atómica.
        """
        if self.payloads.count() > 0:
            return cast(
                List["Payload"], list(self.payloads.order_by(Payload.sequence_index))
            )

        if self.source.type == SourceType.FOLDER:
            # Ejecutar motor físico
            catalog, chunks, members = StatelessPartitioner.partition_folder(
                path, self.config.tg_max_size, settings.exclude_files
            )
            # Guardar en base de datos local
            return self._persist_folder_chunks(chunks, members)
        else:
            # Ejecutar motor físico
            chunks = StatelessPartitioner.partition_file(
                path, self.config.tg_max_size, self.source.md5sum
            )
            # Guardar en base de datos local
            return self._persist_chunks(chunks)

    def _persist_chunks(self, virtual_chunks: List[VirtualChunk]) -> List["Payload"]:
        """Guarda la estructura de payloads del archivo en la base de datos."""

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

    def _persist_folder_chunks(
        self,
        virtual_chunks: List[VirtualChunk],
        virtual_members: List[VirtualTapeMember],
    ) -> List["Payload"]:
        """Guarda la estructura de volúmenes y el índice interno de la carpeta (TapeMembers)."""

        database = self._meta.database

        with db_transaction(database):
            payloads_data = [
                {
                    "job": self,
                    "sequence_index": vc.sequence_index,
                    "start_offset": vc.start_offset,
                    "end_offset": vc.end_offset,
                    "size": vc.size,
                    "filename": vc.filename,
                    "filename_short": vc.filename_short,
                }
                for vc in virtual_chunks
            ]
            Payload.insert_many(payloads_data).execute()

            # Recuperar mapeo id <-> index de los payloads recién guardados
            payload_map = {
                p.sequence_index: p
                for p in cast(
                    list[Payload], self.payloads.order_by(Payload.sequence_index)
                )
            }

            member_inserts = [
                {
                    "source": self.source,
                    "relative_path": vm.relative_path,
                    "size": vm.size,
                    "md5sum": vm.md5sum,
                }
                for vm in virtual_members
            ]

            for batch in batched(member_inserts, 100):
                TapeMember.insert_many(batch).on_conflict_ignore().execute()

            # Obtener mapeo de ruta -> id para asociar los fragmentos (GPS)
            paths = [vm.relative_path for vm in virtual_members]
            db_members = TapeMember.select(
                TapeMember.id, TapeMember.relative_path
            ).where(
                (TapeMember.source == self.source) & (TapeMember.relative_path << paths)  # type: ignore
            )
            member_path_to_id = {m.relative_path: m.id for m in db_members}

            gps_inserts = []
            for vm in virtual_members:
                member_id = member_path_to_id.get(vm.relative_path)
                if not member_id:
                    continue

                for frag in vm.fragments:
                    payload_obj = payload_map.get(frag.vol_idx)
                    if not payload_obj:
                        continue

                    gps_inserts.append(
                        {
                            "member": member_id,
                            "payload": payload_obj,
                            "state": frag.state,
                            "offset_in_volume": frag.offset_in_vol,
                            "bytes_in_volume": frag.bytes_in_volume,
                        }
                    )

            for batch in batched(gps_inserts, 200):
                TapeMemberGPS.insert_many(batch).execute()

        return cast(List[Payload], list(self.payloads.order_by(Payload.sequence_index)))


# El índice compuesto único impide que existan dos jobs activos (deleted_at == 0) para el mismo archivo en el mismo chat.
Job.add_index(Job.source, Job.chat, Job.deleted_at, unique=True)


class Payload(BaseModel):
    """Representa una parte física (trozo) que compone un Job."""

    id: int
    payloads: Generator["Payload", None, None]
    job_id: int

    job = cast(Job, peewee.ForeignKeyField(Job, backref="payloads"))
    md5sum = cast(Optional[str], peewee.CharField(null=True))

    filename = cast(str, peewee.CharField())  # Nombre "Humano" (foto...png.01-10)
    filename_short = cast(str, peewee.CharField())  # Nombre "Técnico" (hash.png.01-10)
    sequence_index = cast(int, peewee.IntegerField())
    start_offset = cast(int, peewee.IntegerField())
    end_offset = cast(int, peewee.IntegerField())
    size = cast(int, peewee.IntegerField())

    @property
    def has_remote(self) -> bool:
        return (
            RemotePayload.select()
            .where(
                (RemotePayload.payload == self) & (RemotePayload.is_orphaned == False)  # noqa: E712
            )
            .exists()
        )

    @staticmethod
    def total_pending_for_job(job: "Job") -> int:
        """Cuenta las piezas que aún no tienen un RemotePayload válido."""
        valid_remotes = RemotePayload.select().where(
            (RemotePayload.payload == Payload.id) & (RemotePayload.is_orphaned == False)  # noqa: E712
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

    payload = peewee.ForeignKeyField(Payload, backref="remotes")
    message_id = cast(int, peewee.IntegerField())
    chat = peewee.ForeignKeyField(TelegramChat, backref="remote_contents")
    owner = cast(
        TelegramUser, peewee.ForeignKeyField(TelegramUser, backref="remote_contents")
    )
    json_metadata = cast(dict, PortableJSONField())

    last_verified_at = cast(Optional[datetime], peewee.DateTimeField(null=True))
    is_orphaned = cast(bool, peewee.BooleanField(default=False))

    def mark_orphaned(self):
        self.is_orphaned = True
        self.save(only=[RemotePayload.is_orphaned, RemotePayload.updated_at])

    def mark_verified(self, message: "Message"):
        if message is None or getattr(message, "empty", True):
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

        last_v = self.last_verified_at
        if last_v.tzinfo is None:
            last_v = last_v.replace(tzinfo=timezone.utc)

        delta = datetime.now(timezone.utc) - last_v
        return delta.total_seconds() < 900

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


class TapeMember(BaseModel):
    """
    Representa un archivo individual dentro de una carpeta archivada.
    Es el registro lógico para búsquedas.
    """

    fragments: List["TapeMemberGPS"]  # Tipado Fake

    id: int
    source = cast("Source", peewee.ForeignKeyField(Source, backref="members"))
    relative_path = cast(str, peewee.CharField())
    size = cast(int, peewee.BigIntegerField())
    md5sum = cast(str, peewee.CharField())

    class Meta:  # type: ignore
        indexes = (
            # Un archivo solo puede estar una vez en una carpeta específica
            (("source", "relative_path"), True),
        )

    @classmethod
    def register_manifest_entries(
        cls, source: "Source", payload: "Payload", entries: list[ManifestEntry]
    ):
        """
        Registra los archivos de un volumen (TapeMembers) y su GPS en el volumen.
        """
        BATCH_SIZE = 100

        for batch in batched(entries, BATCH_SIZE):  # type: ignore
            batch: List[ManifestEntry]
            member_data = [
                {
                    "source": source,
                    "relative_path": e.info.arc_path,
                    "size": e.info.size,
                    "md5sum": e.info.md5sum,
                }
                for e in batch
                if not e.info.is_dir
            ]
            cls.insert_many(member_data).on_conflict_ignore().execute()

        path_to_id = {}
        for batch in batched(entries, BATCH_SIZE):  # type: ignore
            paths = [e.info.arc_path for e in batch]

            # `<<` (operador IN de Peewee)
            query = cls.select(cls.id, cls.relative_path).where(
                (cls.source == source) & (cls.relative_path << paths)  # type: ignore
            )

            for member in query:
                path_to_id[member.relative_path] = member.id

        # Registra el GPS de cada archivo en el volumen
        for batch in batched(entries, BATCH_SIZE):  # type: ignore
            gps_data = [
                {
                    "member": path_to_id[e.info.arc_path],
                    "payload": payload,
                    "state": e.state.value,
                    "offset_in_volume": e.local_window.start,
                    "bytes_in_volume": e.local_window.end,
                }
                for e in batch
                if not e.info.is_dir
            ]
            TapeMemberGPS.insert_many(gps_data).execute()


class TapeMemberGPS(BaseModel):
    member = cast(TapeMember, peewee.ForeignKeyField(TapeMember, backref="fragments"))
    payload = cast("Payload", peewee.ForeignKeyField(Payload, backref="fragments"))

    state = cast(EntryState, EnumField(EntryState))  # type: ignore
    offset_in_volume = cast(int, peewee.BigIntegerField())
    bytes_in_volume = cast(int, peewee.BigIntegerField())


class Claim(BaseModel):
    # 'account:123456789' o 'job:543'
    resource_id = peewee.CharField(primary_key=True)
    resource_type = EnumField(ResourceType)  # type: ignore
    node_id = cast(str, peewee.CharField())
    expires_at = cast(datetime, peewee.DateTimeField())

    @classmethod
    def is_expired(cls, claim: "Claim") -> bool:
        now = datetime.now(timezone.utc)
        exp = claim.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return now > exp
