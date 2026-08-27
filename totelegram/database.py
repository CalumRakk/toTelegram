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
from playhouse.db_url import register_database
from playhouse.postgres_ext import Psycopg3Database

from totelegram.migration import run_migrations

logger = logging.getLogger(__name__)

# Sobrescribimos el registro para que URLs estándar (postgresql://, postgres://)
# utilicen el driver moderno psycopg (v3)
register_database(
    Psycopg3Database, "postgresql", "postgres", "psycopg3", "postgresql+psycopg"
)


def normalize_database_url(
    url_or_path: Optional[str], default_sqlite_path: Path
) -> str:
    """
    Normaliza la configuración de entrada convirtiéndola en una URL de base de datos válida.
    Si recibe una ruta local, la transforma a una URL de conexión de SQLite.
    """
    if not url_or_path:
        abs_default = default_sqlite_path.resolve()
        return f"sqlite:///{abs_default.as_posix()}"

    url_or_path = url_or_path.strip()

    valid_schemes = (
        "sqlite://",
        "postgresql://",
        "postgres://",
        "psycopg3://",
        "postgresql+psycopg://",
    )
    if any(url_or_path.startswith(scheme) for scheme in valid_schemes):
        return url_or_path

    if url_or_path == ":memory:":
        return "sqlite:///:memory:"

    path = Path(url_or_path)
    if not path.is_absolute():
        path = (default_sqlite_path.parent / path).resolve()
    else:
        path = path.resolve()

    return f"sqlite:///{path.as_posix()}"


db_proxy = peewee.Proxy()

# Semáforo global para sincronizar hilos cuando se usa SQLite en disco/memoria
_sqlite_write_lock = threading.RLock()


@contextmanager
def db_transaction(db: peewee.Database):
    """
    Gestor de transacciones seguro para múltiples motores.
    - SQLite: Aplica semáforo en memoria e inicia con 'IMMEDIATE'.
    - PostgreSQL: Utiliza el manejador atómico estándar.
    """
    is_sqlite = isinstance(db, peewee.SqliteDatabase)

    if is_sqlite:
        _sqlite_write_lock.acquire()

    try:
        transaction_type = "IMMEDIATE" if is_sqlite else None
        with db.atomic(transaction_type):
            yield
    finally:
        if is_sqlite:
            _sqlite_write_lock.release()


def init_database_schema(db: peewee.Database, db_url: str):
    """
    Inicializa el esquema de tablas y ejecuta migraciones pendientes.
    Debe invocarse una sola vez al inicio de los flujos de trabajo principales.
    """
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

    run_migrations(db, db_url)


class DatabaseSession:
    """
    Administrador de contexto reentrante para la base de datos basado en URLs.
    Controla el conteo de referencias para evitar cierres prematuros en llamadas anidadas.
    """

    active_db_url: Optional[str] = None
    _ref_count: int = 0
    _lock: threading.Lock = threading.Lock()

    def __init__(self, db_url: str, auto_init_schema: bool = False):
        self.db_url = db_url
        self.auto_init_schema = auto_init_schema
        self.db: Optional[peewee.Database] = None

    def __enter__(self) -> peewee.Database:
        with DatabaseSession._lock:
            # Reutilizar conexión activa si coincide con la URL solicitada y sigue abierta
            if (
                DatabaseSession.active_db_url == self.db_url
                and db_proxy.obj is not None
                and not db_proxy.is_closed()
            ):
                DatabaseSession._ref_count += 1
                if self.auto_init_schema:
                    init_database_schema(db_proxy.obj, self.db_url)
                return db_proxy.obj

            # Si había otra conexión abierta distinta, se cierra
            if db_proxy.obj is not None and not db_proxy.is_closed():
                db_proxy.obj.close()
                DatabaseSession._ref_count = 0

            logger.debug(f"Conectando a base de datos: {self.db_url.split('@')[-1]}")

            parsed = db_parse(self.db_url)
            if parsed.get("engine") == "peewee.SqliteDatabase":
                db_file_path = parsed.get("database")
                if db_file_path and db_file_path != ":memory:":
                    Path(db_file_path).parent.mkdir(parents=True, exist_ok=True)

            self.db = db_connect(self.db_url)

            if isinstance(self.db, peewee.SqliteDatabase):
                self.db.pragma("journal_mode", "wal")
                self.db.pragma("cache_size", -1024 * 64)
                self.db.pragma("synchronous", "NORMAL")
                self.db.pragma("busy_timeout", 30000)
                self.db.pragma("foreign_keys", 1)

            db_proxy.initialize(self.db)

            assert self.db is not None, "Base de datos no inicializada"

            self.db.connect(reuse_if_open=True)

            DatabaseSession.active_db_url = self.db_url
            DatabaseSession._ref_count = 1

            if self.auto_init_schema:
                init_database_schema(self.db, self.db_url)

            return self.db

    def __exit__(self, exc_type, exc_val, exc_tb):
        with DatabaseSession._lock:
            DatabaseSession._ref_count -= 1

            # Solo cerrar físicamente la conexión cuando el último contexto termine
            if DatabaseSession._ref_count <= 0:
                DatabaseSession._ref_count = 0
                if self.db and not self.db.is_closed():
                    self.db.close()

                if db_proxy.obj and not db_proxy.obj.is_closed():
                    db_proxy.obj.close()

                DatabaseSession.active_db_url = None

    def start(self):
        return self.__enter__()

    def close(self):
        return self.__exit__(None, None, None)


class PydanticJSONField(Field):
    field_type = "TEXT"

    def __init__(self, schema_model, *args, **kwargs):
        self.schema_model = schema_model
        super().__init__(*args, **kwargs)

    def db_value(self, value):
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
