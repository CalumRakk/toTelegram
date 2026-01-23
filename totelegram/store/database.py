import logging
from pathlib import Path
from typing import Union

import peewee

logger = logging.getLogger(__name__)
db_proxy = peewee.Proxy()


class DatabaseSession:
    """
    Administrador de contexto para la base de datos SQLite.
    Incluye una protección para evitar escrituras accidentales en producción durante tests.
    """

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)
        self.db = None

    def __enter__(self):
        if db_proxy.obj is not None:
            return db_proxy

        logger.debug(f"Iniciando base de datos en {self.db_path}")

        self.db = peewee.SqliteDatabase(
            str(self.db_path),
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
            self.db.close()

    def start(self):
        return self.__enter__()

    def close(self):
        return self.__exit__(None, None, None)
