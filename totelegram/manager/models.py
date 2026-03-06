import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Generator, List, Optional, Tuple, cast

import peewee
import tartape
from playhouse.sqlite_ext import JSONField
from tartape import Tape
from tartape.schemas import EntryState, ManifestEntry

from totelegram import __version__
from totelegram.common.enums import JobStatus, SourceType, Strategy
from totelegram.telegram.client import parse_message_json_data

if TYPE_CHECKING:
    from totelegram.manager.setting import Settings
    from pyrogram.types import Chat as TgChat
    from pyrogram.types import Message

from totelegram.common.fields import EnumField, PydanticJSONField
from totelegram.common.schemas import StrategyConfig, TapeCatalog
from totelegram.common.utils import batched, create_md5sum_by_hashlib, get_mimetype
from totelegram.manager.database import db_proxy

logger = logging.getLogger(__name__)


class BaseModel(peewee.Model):
    created_at = cast(datetime, peewee.DateTimeField(default=datetime.now))
    updated_at = peewee.DateTimeField(default=datetime.now)

    def save(self, *args, **kwargs):
        self.updated_at = datetime.now()
        return super().save(*args, **kwargs)

    class Meta:
        database = db_proxy


class TelegramChat(BaseModel):
    """
    Representa un destino en Telegram (Canal, Grupo o Mensajes Guardados).
    El 'id' es el BigInteger que provee Telegram directamente.
    """

    id = cast(int, peewee.BigIntegerField(primary_key=True))
    title = cast(str, peewee.CharField(null=True))
    username = cast(Optional[str], peewee.CharField(null=True))
    type = cast(str, peewee.CharField())  # 'private', 'group', 'channel', etc.

    is_public = cast(bool, peewee.BooleanField(default=False))
    last_verified = peewee.DateTimeField(null=True)

    @staticmethod
    def get_or_create_from_chat(tg_chat: "TgChat") -> Tuple["TelegramChat", bool]:
        """
        tg_chat puede ser un objeto Chat de Pyrogram.
        """
        chat, created = TelegramChat.get_or_create(
            id=tg_chat.id,
            defaults={
                "title": tg_chat.title,
                "username": tg_chat.username,
                "type": str(tg_chat.type.value),
                "is_public": True if tg_chat.username else False,
                "last_verified": datetime.now(),
            },
        )
        return chat, created

    def update_from_tg(
        self,
        tg_chat: "TgChat",
    ):
        self.title = tg_chat.title
        self.username = tg_chat.username
        self.type = str(tg_chat.type.value)
        self.is_public = True if tg_chat.username else False
        self.last_verified = datetime.now()
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
    username = cast(str, peewee.CharField(null=True))
    is_premium = cast(bool, peewee.BooleanField(default=False))

    last_seen = peewee.DateTimeField(default=datetime.now)

    @staticmethod
    def get_or_create_from_tg(tg_user) -> "TelegramUser":
        user, created = TelegramUser.get_or_create(
            id=tg_user.id,
            defaults={
                "first_name": tg_user.first_name,
                "username": tg_user.username,
                "is_premium": tg_user.is_premium or False,
            },
        )
        if not created:
            user.first_name = tg_user.first_name
            user.username = tg_user.username
            user.is_premium = tg_user.is_premium or False
            user.last_seen = datetime.now()
            user.save()
        return user


class SourceFile(BaseModel):
    """Representa la identidad única de un recurso (Archivo o Carpeta)."""

    path_str = cast(str, peewee.CharField())
    md5sum = cast(str, peewee.CharField(unique=True))  # MD5 o Fingerprint
    size = cast(int, peewee.BigIntegerField())
    # mtime : Unix timestamp. Es util para la identificar archivo
    mtime = cast(float, peewee.FloatField())
    mimetype = cast(str, peewee.CharField())
    type = cast(SourceType, EnumField(SourceType, default=SourceType.FILE))

    tape_catalog = cast(
        Optional[TapeCatalog], PydanticJSONField(TapeCatalog, null=True)
    )

    @property
    def path(self) -> Path:
        return Path(self.path_str)

    @property
    def is_folder(self) -> bool:
        return self.type == SourceType.FOLDER

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
                    SourceFile.path_str,
                    SourceFile.size,
                    SourceFile.mtime,
                    SourceFile.updated_at,
                ]
            )
        return changed

    @staticmethod
    def get_or_create_from_path(path: Path) -> "SourceFile":
        stat = path.stat()
        current_size = stat.st_size
        current_mtime = stat.st_mtime
        path_str = str(path)

        # Intento rápido por ruta y metadatos
        cached = SourceFile.get_or_none(
            (SourceFile.path_str == path_str)
            & (SourceFile.size == current_size)
            & (SourceFile.mtime == current_mtime)
        )
        if cached:
            return cached

        # Si falló el rápido, calculamos MD5
        md5sum = create_md5sum_by_hashlib(path)

        source = cast(
            Optional[SourceFile], SourceFile.get_or_none(SourceFile.md5sum == md5sum)
        )
        if source:
            source.update_if_needed(path)
            return source

        return SourceFile.create(
            md5sum=md5sum,
            path_str=path_str,
            size=current_size,
            mtime=current_mtime,
            mimetype=get_mimetype(path),
        )

    @staticmethod
    def from_tape(folder_path, tape: Tape):

        exclude_patterns = (
            json.dumps(tape.exclude_patterns)
            if isinstance(tape.exclude_patterns, list)
            else tape.exclude_patterns
        )

        catalog = TapeCatalog(
            fingerprint=tape.fingerprint,
            total_size=tape.total_size,
            total_files=tape.count_files,
            tartape_version=tartape.__version__,
            created_at=tape.created_at,
            exclude_patterns=exclude_patterns,
        )

        source = SourceFile.create(
            path_str=str(folder_path),
            md5sum=catalog.fingerprint,
            size=catalog.total_size,
            mtime=tape.created_at,
            mimetype="application/x-tar",
            tape_catalog=catalog,
            type=SourceType.FOLDER,
        )
        return source


class Job(BaseModel):
    id: int
    payloads: peewee.ModelSelect  # type: ignore

    source = cast(SourceFile, peewee.ForeignKeyField(SourceFile, backref="jobs"))
    chat = peewee.ForeignKeyField(TelegramChat, backref="jobs")

    strategy = cast(Strategy, EnumField(Strategy))
    config = cast(StrategyConfig, PydanticJSONField(StrategyConfig))
    status = cast(JobStatus, EnumField(JobStatus))

    class Meta:  # type: ignore
        # Esto implementa la visión: "Si ya lo subí aquí, no lo subas de nuevo".
        indexes = ((("source", "chat_id"), True),)

    @property
    def path(self) -> Path:
        return Path(self.source.path_str)

    def set_uploaded(self):
        self.status = JobStatus.UPLOADED
        self.save(only=[Job.status, Job.updated_at])

    @staticmethod
    def formalize_intent(
        source: "SourceFile",
        chat: "TelegramChat",
        is_premium: bool,
        tg_limit: int,
    ) -> "Job":
        """
        Crea un Job basado en la estrategía y la configuración de la cuenta.
        """

        strategy = Strategy.evaluate(source.size, tg_limit)
        config = StrategyConfig(
            tg_max_size=tg_limit, user_is_premium=is_premium, app_version=__version__
        )
        return Job.create(
            source=source,
            chat=chat,
            strategy=strategy,
            status=JobStatus.PENDING,
            config=config,
        )

    @staticmethod
    def get_for_source_in_chat(
        source: "SourceFile", chat: "TelegramChat"
    ) -> Optional["Job"]:
        """Devuelve el Job que existe para el source en el chat especificado."""
        return Job.get_or_none((Job.source == source) & (Job.chat == chat))

    def adopt_job(self, job: "Job") -> "Job":
        self.strategy = job.strategy
        self.status = job.status
        self.config = job.config
        self.save(only=[Job.strategy, Job.status, Job.config, Job.updated_at])
        return self


class Payload(BaseModel):
    """Representa una parte física (trozo) que compone un Job."""

    id: int
    payloads: Generator["Payload", None, None]

    job = cast(Job, peewee.ForeignKeyField(Job, backref="payloads"))
    md5sum = cast(Optional[str], peewee.CharField(null=True))

    filename = cast(str, peewee.CharField())  # metadato para evitar consulta extra.
    sequence_index = cast(int, peewee.IntegerField())
    start_offset = cast(int, peewee.IntegerField())
    end_offset = cast(int, peewee.IntegerField())
    size = cast(int, peewee.IntegerField())

    @property
    def has_remote(self) -> bool:
        return RemotePayload.select().where(RemotePayload.payload == self).exists()


class RemotePayload(BaseModel):
    """Representa el Acceso Efectivo: El vínculo entre el Payload y el mensaje en Telegram."""

    id: int
    payload_id: int
    chat_id: int

    payload = peewee.ForeignKeyField(Payload, unique=True, backref="remote")
    message_id = cast(int, peewee.IntegerField())
    chat = peewee.ForeignKeyField(TelegramChat, backref="remote_contents")
    owner = cast(
        TelegramUser, peewee.ForeignKeyField(TelegramUser, backref="remote_contents")
    )
    # Backup completo del objeto Message de Pyrogram
    json_metadata = cast(dict, JSONField())
    last_verified_at = cast(Optional[datetime], peewee.DateTimeField(null=True))

    def mark_verified(self, message: "Message"):
        """Actualiza el timestamp de validación."""
        self.last_verified_at = datetime.now()
        self.json_metadata = json.loads(str(message))
        self.save(
            only=[
                RemotePayload.last_verified_at,
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
        if not self.last_verified_at:
            return False
        delta = datetime.now() - self.last_verified_at
        return delta.total_seconds() < 900  # 15 minutos

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

    id: int
    source = cast("SourceFile", peewee.ForeignKeyField(SourceFile, backref="members"))
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
        cls, source: "SourceFile", payload: "Payload", entries: list[ManifestEntry]
    ):
        """
        Registra los archivos de un volumen (TapeMembers) y su GPS en el volumen.
        """
        BATCH_SIZE = 100

        for batch in batched(entries, BATCH_SIZE):
            batch: List[ManifestEntry]
            member_data = [
                {
                    "source": source,
                    "relative_path": e.arc_path,
                    "size": e.size,
                    "md5sum": e.md5sum,
                }
                for e in batch
                if not e.is_dir
            ]
            cls.insert_many(member_data).on_conflict_ignore().execute()

        path_to_id = {}
        for batch in batched(entries, BATCH_SIZE):
            paths = [e.arc_path for e in batch]

            # `<<` (operador IN de Peewee)
            query = cls.select(cls.id, cls.relative_path).where(
                (cls.source == source) & (cls.relative_path << paths)  # type: ignore
            )

            for member in query:
                path_to_id[member.relative_path] = member.id

        # Registra el GPS de cada archivo en el volumen
        for batch in batched(entries, BATCH_SIZE):
            gps_data = [
                {
                    "member": path_to_id[e.arc_path],
                    "payload": payload,
                    "state": e.state.value,
                    "offset_in_volume": e.offset_in_volume,
                    "bytes_in_volume": e.bytes_in_volume,
                }
                for e in batch
                if not e.is_dir
            ]
            TapeMemberGPS.insert_many(gps_data).execute()


class TapeMemberGPS(BaseModel):

    member = cast(TapeMember, peewee.ForeignKeyField(TapeMember, backref="fragments"))
    payload = cast("Payload", peewee.ForeignKeyField(Payload, backref="fragments"))

    state = cast(EntryState, EnumField(EntryState))
    offset_in_volume = cast(int, peewee.BigIntegerField())
    bytes_in_volume = cast(int, peewee.BigIntegerField())
