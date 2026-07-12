import logging
import shutil
from datetime import datetime
from pathlib import Path

import peewee

from totelegram import __CURRENT_DB_VERSION__

logger = logging.getLogger(__name__)


class SchemaVersion(peewee.Model):
    """
    Tabla unificada de control de versiones de esquema.
    Se utilizará de manera agnóstica para todos los motores a partir de la v2.
    """

    version = peewee.IntegerField(primary_key=True)
    applied_at = peewee.DateTimeField(
        constraints=[peewee.SQL("DEFAULT CURRENT_TIMESTAMP")]
    )

    class Meta:
        table_name = "schema_version"


def run_migrations(db: peewee.Database, db_url: str):
    """
    Orquesta las migraciones de base de datos diferenciando entre SQLite (histórico)
    y PostgreSQL (esquema inicializado en la versión más reciente).
    """
    is_sqlite = isinstance(db, peewee.SqliteDatabase)

    if is_sqlite:
        _run_sqlite_historical_migrations(db, db_url)
    else:
        _run_generic_migrations(db)


def _run_sqlite_historical_migrations(db: peewee.SqliteDatabase, db_url: str):
    """
    Mantiene la compatibilidad de SQLite utilizando PRAGMA user_version.
    """
    cursor = db.execute_sql("PRAGMA user_version")
    db_version = cursor.fetchone()[0]

    if db_version == __CURRENT_DB_VERSION__:
        return

    if db_version > __CURRENT_DB_VERSION__:
        msg = (
            f"Incompatibilidad detectada: La base de datos es versión {db_version}, "
            f"pero este programa solo soporta hasta la versión {__CURRENT_DB_VERSION__}.\n"
            "ACCIÓN: Actualiza toTelegram o borra la base de datos para empezar de cero."
        )
        logger.critical(msg)
        raise RuntimeError(msg)

    # Intenta realizar un backup antes de alterar las tablas de SQLite en disco
    db_file_path = getattr(db, "database", None)
    if db_file_path and db_file_path != ":memory:":
        try:
            path = Path(db_file_path)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{path.name}.v{db_version}.{timestamp}.bak"
            backup_path = path.parent / backup_name
            shutil.copy(path, backup_path)
            logger.info(f"Backup de seguridad creado: {backup_path.name}")
        except Exception as e:
            logger.warning(f"No se pudo crear el backup de base de datos: {e}")

    try:
        from totelegram.database import db_transaction

        with db_transaction(db):
            if db_version < 1:
                _migrate_to_v1(db)

            if db_version < 2:
                _migrate_to_v2(db)

            db.execute_sql(f"PRAGMA user_version = {__CURRENT_DB_VERSION__}")
            logger.info(
                f"Base de datos SQLite migrada con éxito a la versión {__CURRENT_DB_VERSION__}"
            )

    except Exception as e:
        logger.error(f"Fallo crítico en la migración de SQLite: {e}")
        raise e


def _run_generic_migrations(db: peewee.Database):
    """
    Inicializa o actualiza bases de datos agnósticas (PostgreSQL).
    Si es una base de datos nueva, la marca directamente en la versión actual.
    """
    # Registrar la tabla de control de versión en la base de datos activa
    SchemaVersion._meta.database = db
    db.create_tables([SchemaVersion], safe=True)

    # Buscar la última versión registrada
    latest_record = (
        SchemaVersion.select().order_by(SchemaVersion.version.desc()).first()
    )

    if latest_record is None:
        # La base de datos es nueva, ya se creó con el esquema más reciente de models.py
        with db.atomic():
            SchemaVersion.create(version=__CURRENT_DB_VERSION__)
        logger.info(
            f"Base de datos inicializada directamente en la versión de esquema {__CURRENT_DB_VERSION__}"
        )
        return

    db_version = latest_record.version

    if db_version == __CURRENT_DB_VERSION__:
        return

    if db_version < __CURRENT_DB_VERSION__:
        # Aquí se ejecutaría la lógica para futuras migraciones (v3, v4, etc.)
        # abstrayendo las sentencias según el motor.
        pass


def _migrate_to_v1(db):
    logger.info("Aplicando cambios de esquema para soporte multi-worker (v1)...")
    db.execute_sql("ALTER TABLE payload ADD COLUMN status TEXT DEFAULT 'PENDING'")
    db.execute_sql("ALTER TABLE payload ADD COLUMN claimed_by TEXT")

    # Sincronizar datos: Si tiene un RemotePayload, ya está subido.
    db.execute_sql(
        """
        UPDATE payload SET status = 'UPLOADED'
        WHERE id IN (SELECT payload_id FROM remotepayload)
        """
    )


def _migrate_to_v2(db):
    logger.info("Migrando a V2: Eliminando gestión de estado manual...")
    try:
        # SQLite soporta DROP COLUMN a partir de la versión 3.35.0 (2021)
        db.execute_sql("ALTER TABLE payload DROP COLUMN status")
        db.execute_sql("ALTER TABLE payload DROP COLUMN claimed_by")
    except Exception as e:
        logger.debug(
            f"DROP COLUMN no soportado en esta versión de SQLite, ignorando: {e}"
        )
