import enum
import json
import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import peewee
from peewee import Field
from playhouse.db_url import connect as db_connect
from playhouse.db_url import parse as db_parse

from totelegram.migration import run_migrations

logger = logging.getLogger(__name__)


def normalize_database_url(
    url_or_path: Optional[str], default_sqlite_path: Path
) -> str:
    """
    Normaliza la configuración de entrada convirtiéndola en una URL de base de datos válida.
    Si recibe una ruta local, la transforma a una URL de conexión de SQLite.
    """
    if not url_or_path:
        # Si no se define nada, usamos la ruta por defecto en formato URL
        abs_default = default_sqlite_path.resolve()
        return f"sqlite:///{abs_default.as_posix()}"

    url_or_path = url_or_path.strip()

    # Si ya tiene formato de esquema de conexión conocido, lo dejamos pasar sin cambios
    if (
        url_or_path.startswith("sqlite://")
        or url_or_path.startswith("postgresql://")
        or url_or_path.startswith("postgres://")
    ):
        return url_or_path

    if url_or_path == ":memory:":
        return "sqlite:///:memory:"

    # Se asume que es una ruta de archivo local para SQLite
    path = Path(url_or_path)
    if not path.is_absolute():
        # Si es relativa, la resolvemos respecto al directorio de trabajo por defecto
        path = (default_sqlite_path.parent / path).resolve()
    else:
        path = path.resolve()

    return f"sqlite:///{path.as_posix()}"


db_proxy = peewee.Proxy()

# Semáforo global para sincronizar hilos cuando se usa SQLite
_sqlite_write_lock = threading.RLock()


@contextmanager
def db_transaction(db: peewee.Database):
    """
    Gestor de transacciones seguro para múltiples motores.
    - SQLite: Aplica semáforo en memoria e inicia con 'IMMEDIATE' para evitar bloqueos concurrentes.
    - PostgreSQL/Otros: Delegación nativa y limpia mediante 'BEGIN' estándar (sin parámetros).
    """
    is_sqlite = isinstance(db, peewee.SqliteDatabase)

    if is_sqlite:
        _sqlite_write_lock.acquire()

    try:
        # SQLite requiere IMMEDIATE para concurrencia en disco.
        # Postgres requiere None para que Peewee ejecute un "BEGIN" limpio y estándar.
        transaction_type = "IMMEDIATE" if is_sqlite else None

        with db.atomic(transaction_type):
            yield
    finally:
        if is_sqlite:
            _sqlite_write_lock.release()


class DatabaseSession:
    """
    Administrador de contexto para la base de datos basado en URLs.
    Detecta automáticamente SQLite o PostgreSQL y aplica configuraciones específicas.
    """

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.db = None

    def __enter__(self) -> peewee.Database:
        if db_proxy.obj is not None:
            current_db_url = getattr(db_proxy.obj, "database", None)

            # Si la base de datos ya coincide y está abierta, la reutilizamos
            if current_db_url == self.db_url and not db_proxy.is_closed():
                return db_proxy.obj

            if not db_proxy.obj.is_closed():
                db_proxy.obj.close()

        logger.debug("Conectando a base de datos mediante URL...")

        # Analizar si es una ruta local de SQLite para asegurar que el directorio exista
        parsed = db_parse(self.db_url)
        if parsed.get("engine") == "peewee.SqliteDatabase":
            db_file_path = parsed.get("database")
            if db_file_path and db_file_path != ":memory:":
                Path(db_file_path).parent.mkdir(parents=True, exist_ok=True)

        # Conectar dinámicamente usando playhouse.db_url
        self.db = db_connect(self.db_url)

        # Configuración específica según el motor
        if isinstance(self.db, peewee.SqliteDatabase):
            # Optimización de rendimiento para SQLite local
            self.db.pragma("journal_mode", "wal")
            self.db.pragma("cache_size", -1024 * 64)
            self.db.pragma("synchronous", "NORMAL")
            self.db.pragma("busy_timeout", 30000)
            self.db.pragma("foreign_keys", 1)

        db_proxy.initialize(self.db)
        self.db.connect(reuse_if_open=True)

        # Importaciones tardías para registrar los modelos
        from totelegram.models import (
            Claim,
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
                Claim,
            ],
            safe=True,
        )

        run_migrations(self.db, self.db_url)
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
        if value is None:
            return None

        if isinstance(value, str):
            return self.schema_model.model_validate_json(value)
        return self.schema_model.model_validate(value)


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
