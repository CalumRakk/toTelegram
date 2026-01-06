import logging

import peewee

from totelegram.setting import Settings

logger = logging.getLogger(__name__)
db_proxy = peewee.Proxy()


def init_database(settings: Settings):
    from totelegram.models import Job, Payload, RemotePayload, SourceFile

    logger.info(f"Iniciando base de datos en {settings.database_path}")
    database = peewee.SqliteDatabase(str(settings.database_path))
    db_proxy.initialize(database)

    db_proxy.create_tables([SourceFile, Job, Payload, RemotePayload], safe=True)
    logger.info("Base de datos inicializada correctamente")
    db_proxy.close()
