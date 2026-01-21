import logging

import peewee

from totelegram.core.setting import Settings

logger = logging.getLogger(__name__)
db_proxy = peewee.Proxy()


class DatabaseSession:
    """
    Administrador de contexto para la base de datos SQLite.
    Se encarga de inicializar, conectar, crear tablas si no existen
    y cerrar la conexión de forma segura al finalizar.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = None

    def __enter__(self):
        if db_proxy.obj is not None:
            return db_proxy

        logger.info(f"Iniciando base de datos en {self.settings.database_path}")

        self.db = peewee.SqliteDatabase(
            str(self.settings.database_path),
            pragmas={"journal_mode": "wal", "cache_size": -1024 * 64},
            timeout=10,
        )

        db_proxy.initialize(self.db)

        self.db.connect()

        from totelegram.store.models import (
            Job,
            Payload,
            RemotePayload,
            SourceFile,
            TelegramChat,
            TelegramUser,
        )

        db_proxy.create_tables(
            [SourceFile, Job, Payload, RemotePayload, TelegramChat, TelegramUser],
            safe=True,
        )
        return self.db

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.db and not self.db.is_closed():
            logger.debug("Cerrando conexión a base de datos...")
            self.db.close()
            logger.info("Base de datos cerrada correctamente.")
