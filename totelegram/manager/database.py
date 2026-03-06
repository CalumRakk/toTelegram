import logging
from pathlib import Path
from typing import Literal, Union

import peewee

logger = logging.getLogger(__name__)
db_proxy = peewee.Proxy()


class DatabaseSession:
    """Administrador de contexto para la base de datos. Encapsula la inicializacion, creacion de tablas y su cierre."""

    def __init__(self, db_path: Union[Union[str, Path], Literal[":memory:"]]):
        self.db_path = Path(db_path) if db_path != ":memory:" else db_path
        self.db = None

    def __enter__(self) -> peewee.SqliteDatabase:
        if db_proxy.obj is not None:
            current_db_path = getattr(db_proxy.obj, "database", None)

            # Si pedimmos la misma DB y sigue viva, la reutilizamos.
            if current_db_path == str(self.db_path) and not db_proxy.is_closed():
                return db_proxy

            # Si llegamos aquí, es una DB distinta o una de ':memory:' que ya fue cerrada.
            if not db_proxy.obj.is_closed():
                db_proxy.obj.close()

        logger.debug(f"Iniciando base de datos en {self.db_path}")

        if isinstance(self.db_path, Path):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db = peewee.SqliteDatabase(
            str(self.db_path),
            pragmas={"journal_mode": "wal", "cache_size": -1024 * 64},
            timeout=10,
        )

        db_proxy.initialize(self.db)
        self.db.connect()

        from totelegram.manager.models import (
            Job,
            Payload,
            RemotePayload,
            Source,
            TapeMember,
            TapeMemberGPS,
            TelegramChat,
            TelegramUser,
        )

        db_proxy.create_tables(
            [
                Source,
                Job,
                Payload,
                RemotePayload,
                TelegramChat,
                TelegramUser,
                TapeMember,
                TapeMemberGPS,
            ],
            safe=True,
        )
        return self.db

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.db and not self.db.is_closed():
            self.db.close()

        if db_proxy.obj and not db_proxy.obj.is_closed():
            db_proxy.obj.close()

    def start(self):
        return self.__enter__()

    def close(self):
        return self.__exit__(None, None, None)
