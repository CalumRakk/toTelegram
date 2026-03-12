import enum
import json
import logging
from pathlib import Path
from typing import Literal, Union

import peewee
from peewee import Field

from totelegram.migration import run_migrations

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
                return db_proxy.obj

            # Si llegamos aquí, es una DB distinta o una de ':memory:' que ya fue cerrada.
            if not db_proxy.obj.is_closed():
                db_proxy.obj.close()

        logger.debug(f"Iniciando base de datos en {self.db_path}")

        if isinstance(self.db_path, Path):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db = peewee.SqliteDatabase(
            str(self.db_path),
            pragmas={
                "journal_mode": "wal",  # Permite leer mientras otro escribe
                "cache_size": -1024 * 64,
                "synchronous": "NORMAL",
                "busy_timeout": 30000,  # Esperar 30s si está bloqueada
                "foreign_keys": 1,  # Asegurar integridad referencial
            },
            timeout=10,
        )

        db_proxy.initialize(self.db)
        self.db.connect()

        from totelegram.models import (
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

        run_migrations(self.db, self.db_path)
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


class PydanticJSONField(Field):
    """
    Campo personalizado de Peewee.
    DB: Guarda JSON String (TEXT).
    Python: Usa objetos Pydantic validados.
    """

    field_type = "TEXT"

    def __init__(self, schema_model, *args, **kwargs):
        self.schema_model = schema_model
        super().__init__(*args, **kwargs)

    def db_value(self, value):
        """Python -> DB"""
        if hasattr(value, "model_dump_json"):
            return value.model_dump_json()
        if value is None:
            return None
        return json.dumps(value)

    def python_value(self, value):
        """DB -> Python"""
        if value is None:
            return None
        try:
            # Si viene como string desde la DB
            if isinstance(value, str):
                return self.schema_model.model_validate_json(value)
            # Si viene como dict (algunos drivers)
            return self.schema_model.model_validate(value)
        except Exception:
            return self.schema_model()


class EnumField(peewee.CharField):
    """
    Enum-like field for Peewee
    """

    def __init__(self, enum: type[enum.Enum], *args, **kwargs):
        self.enum = enum
        kwargs.setdefault("max_length", max(len(e.value) for e in enum))
        super().__init__(*args, **kwargs)

    def db_value(self, value):
        if value is None:
            return None
        if isinstance(value, self.enum):
            return value.value
        return self.enum(value).value

    def python_value(self, value):
        if value is None:
            return None
        return self.enum(value)
