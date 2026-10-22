import enum
import json
import logging
import threading
from contextlib import contextmanager
from pathlib import Path

import peewee
from peewee import Field

logger = logging.getLogger(__name__)

db_proxy = peewee.Proxy()

# Semáforo global para sincronizar escrituras en SQLite entre hilos de un mismo proceso
_sqlite_write_lock = threading.RLock()

# Pragmas esenciales para alto rendimiento y concurrencia local segura
SQLITE_PRAGMAS = {
    "journal_mode": "wal",
    "cache_size": -1024 * 64,  # 64 MB de caché en memoria
    "synchronous": "normal",
    "busy_timeout": 60000,  # 60 segundos de tolerancia ante bloqueos
    "foreign_keys": 1,
}


@contextmanager
def db_transaction(db: peewee.Database):
    """
    Gestor de transacciones seguro para SQLite.
    Aplica el semáforo en memoria e inicia la transacción con 'IMMEDIATE'
    para evitar conflictos de escalado de bloqueos.
    """
    with _sqlite_write_lock, db.atomic("IMMEDIATE"):
        yield


def init_database_schema(db: peewee.Database, db_url: str | None = None):
    """Inicializa el esquema y ejecuta migraciones pendientes."""
    from totelegram.migration import setup_database_schema

    setup_database_schema(db)


def _create_sqlite_database(target: Path | str) -> peewee.SqliteDatabase:
    """Crea una instancia de SqliteDatabase con los pragmas de alto rendimiento."""
    str_target = str(target).strip()
    if str_target.startswith("sqlite:///"):
        str_target = str_target.replace("sqlite:///", "")

    if str_target != ":memory:":
        path = Path(str_target)
        path.parent.mkdir(parents=True, exist_ok=True)
        return peewee.SqliteDatabase(str(path), pragmas=SQLITE_PRAGMAS)

    return peewee.SqliteDatabase(":memory:", pragmas=SQLITE_PRAGMAS)


class DatabaseSession:
    """
    Administrador de contexto reentrante para SQLite.
    Controla el conteo de referencias para evitar cierres prematuros en llamadas anidadas.
    """

    active_db_target: str | None = None
    _ref_count: int = 0
    _lock: threading.Lock = threading.Lock()

    def __init__(self, db_target: Path | str, auto_init_schema: bool = False):
        self.db_target = str(db_target)
        self.auto_init_schema = auto_init_schema
        self.db: peewee.SqliteDatabase | None = None

    def __enter__(self) -> peewee.Database:
        with DatabaseSession._lock:
            # Reutilizar conexión activa si coincide con el destino solicitado y sigue abierta
            if (
                DatabaseSession.active_db_target == self.db_target
                and db_proxy.obj is not None
                and not db_proxy.is_closed()
            ):
                DatabaseSession._ref_count += 1
                if self.auto_init_schema:
                    init_database_schema(db_proxy.obj, self.db_target)
                return db_proxy.obj

            # Si había otra conexión abierta distinta, se cierra
            if db_proxy.obj is not None and not db_proxy.is_closed():
                db_proxy.obj.close()
                DatabaseSession._ref_count = 0

            logger.debug(f"Conectando a base de datos SQLite: {self.db_target}")

            self.db = _create_sqlite_database(self.db_target)
            db_proxy.initialize(self.db)

            assert self.db is not None, "Base de datos SQLite no inicializada"

            self.db.connect(reuse_if_open=True)

            DatabaseSession.active_db_target = self.db_target
            DatabaseSession._ref_count = 1

            if self.auto_init_schema:
                init_database_schema(self.db, self.db_target)

            return self.db

    def __exit__(self, exc_type, exc_val, exc_tb):
        with DatabaseSession._lock:
            DatabaseSession._ref_count -= 1

            if DatabaseSession._ref_count <= 0:
                DatabaseSession._ref_count = 0
                if self.db and not self.db.is_closed():
                    self.db.close()

                if db_proxy.obj and not db_proxy.obj.is_closed():
                    db_proxy.obj.close()

                DatabaseSession.active_db_target = None

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
