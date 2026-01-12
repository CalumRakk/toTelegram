import logging

import peewee

from totelegram.core.setting import Settings

logger = logging.getLogger(__name__)
db_proxy = peewee.Proxy()


def init_database(settings: Settings):
    from totelegram.store.models import Job, Payload, RemotePayload, SourceFile

    logger.info(f"Iniciando base de datos en {settings.database_path}")
    database = peewee.SqliteDatabase(
        str(settings.database_path),
        pragmas={"journal_mode": "wal", "cache_size": -1024 * 64},
        timeout=10,
    )
    # `"cache_size": -1024 * 64` = Usa hasta 64 MB de memoria RAM para la caché
    db_proxy.initialize(database)

    db_proxy.create_tables([SourceFile, Job, Payload, RemotePayload], safe=True)
    logger.info("Base de datos inicializada correctamente")
    db_proxy.close()
