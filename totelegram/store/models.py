import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Generator, Optional, Tuple, cast

import peewee
from playhouse.sqlite_ext import JSONField

from totelegram import __version__
from totelegram.core.enums import JobStatus, Strategy

if TYPE_CHECKING:
    from totelegram.core.setting import Settings
    from pyrogram.types import Chat as TgChat
    from pyrogram.types import Message

from totelegram.core.schemas import StrategyConfig
from totelegram.store.database import db_proxy
from totelegram.store.fields import EnumField, PydanticJSONField
from totelegram.utils import create_md5sum_by_hashlib, get_mimetype

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
    def get_or_create_from_tg(tg_chat: "TgChat") -> Tuple["TelegramChat", bool]:
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
    """Representa la existencia física de un archivo en el disco local."""

    path_str = cast(str, peewee.CharField())
    md5sum = cast(str, peewee.CharField(unique=True))  # El ancla de todo el sistema
    size = cast(int, peewee.IntegerField())
    # mtime : Unix timestamp. Es util para la identificar archivo en el disco con
    mtime = cast(float, peewee.FloatField())
    mimetype = cast(str, peewee.CharField())

    @property
    def path(self) -> Path:
        return Path(self.path_str)

    def update_if_needed(self, path: Path) -> bool:
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
        source, created = SourceFile.get_or_create(
            md5sum=md5sum,
            defaults={
                "path_str": path_str,
                "size": current_size,
                "mtime": current_mtime,
                "mimetype": get_mimetype(path),
            },
        )
        if not created:
            source.update_if_needed(path)

        return source


class Job(BaseModel):
    """Representa la intención de disponibilizar un SourceFile en un Chat específico."""

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
    def create_contract(
        source: "SourceFile",
        chat: "TelegramChat",
        is_premium: bool,
        settings: "Settings",
    ) -> "Job":
        """
        Crea un Job basado en la estrategía y la configuración de la cuenta.
        """
        # Determinamos el límite técnico según el estado de la cuenta
        tg_limit = (
            settings.TG_MAX_SIZE_PREMIUM if is_premium else settings.TG_MAX_SIZE_NORMAL
        )

        # Evaluamos la estrategia
        strategy = Strategy.evaluate(source.size, tg_limit)

        # Empaquetamos la configuración que regirá este Job para siempre (ADR-002)
        config = StrategyConfig(
            tg_max_size=tg_limit, user_is_premium=is_premium, app_version=__version__
        )

        logger.info(f"JOB CONTRACT: {source.md5sum} -> {strategy} (Limit: {tg_limit})")

        return Job.create(
            source=source,
            chat=chat,
            strategy=strategy,
            status=JobStatus.PENDING,
            config=config,
        )


class Payload(BaseModel):
    """Representa una parte física (trozo) que compone un Job."""

    id: int

    # En realidad es un ModelSelect, pero se tipa asi para pylance
    payloads: Generator["Payload", None, None]

    job = cast(Job, peewee.ForeignKeyField(Job, backref="payloads"))
    sequence_index = peewee.IntegerField()
    temp_path = cast(str, peewee.CharField(null=True))
    md5sum = peewee.CharField()  # MD5 del trozo específico
    size = peewee.IntegerField()

    @property
    def path(self) -> Path:
        return Path(self.temp_path)

    @staticmethod
    def create_payloads(job: Job, paths: list[Path]) -> list["Payload"]:
        """Crea registros base para las partes. El MD5 es obligatorio."""
        saved_payloads = []
        with db_proxy.atomic():
            for idx, path in enumerate(paths):
                # Si es single, nos ahorramos tener que generar nuevamente el md5
                if job.strategy == Strategy.SINGLE:
                    md5_p, size_p = job.source.md5sum, job.source.size
                else:
                    md5_p, size_p = create_md5sum_by_hashlib(path), path.stat().st_size

                payload = Payload.create(
                    job=job,
                    sequence_index=idx,
                    temp_path=str(path),
                    md5sum=md5_p,
                    size=size_p,
                )
                saved_payloads.append(payload)
        return saved_payloads


class RemotePayload(BaseModel):
    """Representa el Acceso Efectivo: El vínculo entre el Payload y el mensaje en Telegram."""

    payload_id: int
    chat_id: int

    payload = peewee.ForeignKeyField(Payload, unique=True, backref="remote")
    message_id = cast(int, peewee.IntegerField())
    chat = peewee.ForeignKeyField(TelegramChat, backref="remote_contents")
    owner = cast(
        TelegramUser, peewee.ForeignKeyField(TelegramUser, backref="remote_contents")
    )
    json_metadata = cast(
        dict, JSONField()
    )  # Backup completo del objeto Message de Pyrogram

    # TODO: Se ha elimiano los campos source_message_id y is_forward, porque
    # es dificil se seguir cuando se hace un reenvio masivo.
    # source_message_id = cast(Optional[int], peewee.IntegerField(null=True))
    # is_forward = cast(bool, peewee.BooleanField(default=False))

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

    # @staticmethod
    # def register_forward(
    #     payload: Payload, tg_message: "Message", source_msg_id: int, owner: TelegramUser
    # ):
    #     """Registra un reenvío como una nueva entrada de acceso."""
    #     return RemotePayload.create(
    #         payload=payload,
    #         message_id=tg_message.id,
    #         chat_id=tg_message.chat.id,
    #         owner=owner,
    #         source_message_id=source_msg_id,
    #         is_forward=True,
    #         json_metadata=json.loads(str(tg_message)),
    #     )
