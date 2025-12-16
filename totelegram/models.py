import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Generator, cast

import peewee
from playhouse.sqlite_ext import JSONField

if TYPE_CHECKING:
    from totelegram.setting import Settings

from totelegram.enums import EnumField, JobStatus, PydanticJSONField, Strategy
from totelegram.schemas import StrategyConfig
from totelegram.utils import create_md5sum_by_hashlib

logger = logging.getLogger(__name__)
db_proxy = peewee.Proxy()


def init_database(settings: Settings):
    logger.info(f"Iniciando base de datos en {settings.database_path}")
    database = peewee.SqliteDatabase(str(settings.database_path))

    db_proxy.initialize(database)

    # Creamos las tablas con los nuevos modelos
    db_proxy.create_tables([SourceFile, Job, Payload, RemotePayload], safe=True)
    logger.info("Base de datos inicializada correctamente")
    db_proxy.close()


class BaseModel(peewee.Model):
    created_at = peewee.DateTimeField(default=datetime.now)
    updated_at = peewee.DateTimeField(default=datetime.now)

    def save(self, *args, **kwargs):
        self.updated_at = datetime.now()
        return super().save(*args, **kwargs)

    class Meta:
        database = db_proxy


class SourceFile(BaseModel):
    path_str = cast(str, peewee.CharField())
    md5sum = cast(str, peewee.CharField(unique=True))
    size = cast(int, peewee.IntegerField())
    mtime = cast(float, peewee.FloatField())
    mimetype = cast(str, peewee.CharField(null=True))

    @property
    def path(self) -> Path:
        return Path(self.path_str)

    def update_if_needed(self, path: Path) -> bool:
        """
        Actualiza los metadatos del SourceFile si han cambiado.
        Retorna True si hubo cambios, False si no.
        """
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
        """
        Busca o crea el registro del archivo físico (SourceFile).
        """
        stat = path.stat()
        current_size = stat.st_size
        current_mtime = stat.st_mtime
        path_str = str(path)

        cached = SourceFile.get_or_none(
            (SourceFile.path_str == path_str)
            & (SourceFile.size == current_size)
            & (SourceFile.mtime == current_mtime)
        )
        if cached:
            logger.debug(f"CACHE HIT (SourceFile): {path.name}")
            return cached

        md5sum = create_md5sum_by_hashlib(path)
        source: SourceFile = SourceFile.get_or_none(SourceFile.md5sum == md5sum)
        if source:
            source.update_if_needed(path)
            return source

        logger.debug(f"Creando nuevo SourceFile para: {path.name}")
        source = SourceFile.create(
            path_str=path_str,
            md5sum=md5sum,
            size=current_size,
            mtime=current_mtime,
        )
        return source


class Job(BaseModel):
    id: int
    payloads: Generator["Payload"]

    source = cast(SourceFile, peewee.ForeignKeyField(SourceFile))
    strategy = cast(Strategy, EnumField(Strategy))
    config = cast(StrategyConfig, PydanticJSONField(StrategyConfig))
    status = cast(JobStatus, EnumField(JobStatus))
    created_at = peewee.DateTimeField()

    @property
    def path(self) -> Path:
        """Devuelve la ruta del archivo fuente asociado al Job."""
        return Path(self.source.path_str)

    def set_splitted(self):
        self.status = JobStatus.SPLITTED
        self.save(only=[Job.status, Job.updated_at])

    @classmethod
    def get_or_create_from_source(
        cls, source: SourceFile, settings: "Settings"
    ) -> "Job":
        """
        Determina la estrategia basada en la configuración y
        crea o recupera el Job adecuado.
        """
        strategy = Strategy.SINGLE
        if source.size > settings.max_filesize_bytes:
            strategy = Strategy.CHUNKED

        job_config = StrategyConfig(
            max_filesize_bytes=settings.max_filesize_bytes,
            upload_limit_rate_kbps=settings.upload_limit_rate_kbps,
            chat_id=str(settings.chat_id),
        )

        # Consulta (get_or_create de Peewee)
        job, created = cls.get_or_create(
            source=source,
            strategy=strategy,
            defaults={
                "config": job_config,
                "status": JobStatus.PENDING,
            },
        )

        if created:
            logger.info(
                f"Creado nuevo Job {job.id} para {source.path.name} "
                f"[Estrategia: {strategy.value}]"
            )
        else:
            logger.debug(f"Job existente recuperado: {job.id=} - {job.status=}")

        return job


class Payload(BaseModel):
    id: int

    job = cast(Job, peewee.ForeignKeyField(Job, backref="payloads"))
    sequence_index = peewee.IntegerField()
    temp_path = cast(str, peewee.CharField(null=True))
    md5sum = peewee.CharField()
    size = peewee.IntegerField()

    @property
    def path(self) -> Path:
        """Devuelve la ruta del archivo temporal asociado al Payload."""
        return Path(self.temp_path)

    @staticmethod
    def create_payloads(job: Job, paths: list[Path]) -> list["Payload"]:
        """
        Guarda los Payloads (piezas o single-file) en la base de datos vinculados al Job.
        """
        logger.info(f"Guardando {len(paths)} payloads para el Job {job.id}…")

        saved_payloads = []
        with db_proxy.atomic():
            for idx, path in enumerate(paths):
                is_single = job.strategy == Strategy.SINGLE
                is_pieces = job.strategy == Strategy.CHUNKED
                if is_single and idx == 0 and path == job.source.path:
                    md5_payload = job.source.md5sum
                    size_payload = job.source.size
                elif is_pieces:
                    md5_payload = create_md5sum_by_hashlib(path)
                    size_payload = path.stat().st_size
                else:
                    raise ValueError("Estrategia de Job desconocida al crear Payloads.")

                payload = Payload.create(
                    job=job,
                    sequence_index=idx,
                    temp_path=str(path),
                    md5sum=md5_payload,
                    size=size_payload,
                )
                saved_payloads.append(payload)

        logger.info(f"Se guardaron {len(saved_payloads)} payloads correctamente")
        return saved_payloads


class RemotePayload(BaseModel):
    payload = peewee.ForeignKeyField(Payload, unique=True)
    message_id = cast(int, peewee.IntegerField())
    chat_id = cast(int, peewee.IntegerField())
    json_metadata = cast(dict, JSONField())

    @staticmethod
    def register_upload(payload: Payload, tg_message) -> None:
        """
        Registra en la BD que un Payload se subió exitosamente.
        """
        logger.debug(f"Registrando RemotePayload para payload ID: {payload.id}")

        json_metadata = json.loads(str(tg_message))
        try:
            remote_payload = RemotePayload.create(
                payload=payload,
                message_id=tg_message.id,
                chat_id=tg_message.chat.id,
                json_metadata=json_metadata,
            )
            return remote_payload
        except Exception as e:
            logger.error(f"Error al guardar RemotePayload en BD: {e}")
            raise
