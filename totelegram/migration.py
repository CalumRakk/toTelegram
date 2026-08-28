import enum
import logging
import shutil
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import peewee
from playhouse.migrate import PostgresqlMigrator, SqliteMigrator, migrate

from totelegram import __CURRENT_DB_VERSION__

logger = logging.getLogger(__name__)

# ID numérico único constante para PostgreSQL Advisory Locks en toTelegram
MIGRATION_ADVISORY_LOCK_ID = 84920492

# Lock local en memoria para proteger SQLite contra accesos concurrentes de subprocesos
_sqlite_migration_lock = threading.RLock()


class DatabaseState(str, enum.Enum):
    """Estados del ciclo de vida y compatibilidad de la base de datos."""

    FRESH = "fresh"  # BD vacía, requiere inicialización completa
    LEGACY_SQLITE = "legacy_sqlite"  # SQLite antiguo (con PRAGMA user_version)
    OUTDATED = "outdated"  # Requiere aplicar migraciones pendientes
    UP_TO_DATE = "up_to_date"  # Lista para operar (cero DDL necesario)
    AHEAD = "ahead"  # Versión futura (bloquear ejecución)
    CORRUPTED = "corrupted"  # Tablas existentes pero esquema inconsistente


class IncompatibleDatabaseError(RuntimeError):
    """Excepción lanzada cuando la base de datos es más reciente que el cliente."""

    pass


@dataclass
class DatabaseInspectionReport:
    """Reporte detallado del diagnóstico de la base de datos."""

    state: DatabaseState
    current_version: int
    target_version: int
    engine_name: str
    details: Optional[str] = None

    @property
    def is_operational(self) -> bool:
        return self.state == DatabaseState.UP_TO_DATE


class SchemaVersion(peewee.Model):
    """
    Tabla canónica y unificada para control de versiones.
    Mantiene el historial de versiones aplicadas tanto en SQLite como en PostgreSQL.
    """

    version = peewee.IntegerField(primary_key=True)
    applied_at = peewee.DateTimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table_name = "schema_version"


# INSPECTOR DE SOLO LECTURA


def inspect_database(db: peewee.Database) -> DatabaseInspectionReport:
    """
    Inspecciona la base de datos de manera estrictamente de SOLO LECTURA.
    No ejecuta DDL ni transacciones de escritura.
    """
    SchemaVersion._meta.database = db
    engine_name = "sqlite" if isinstance(db, peewee.SqliteDatabase) else "postgresql"
    target_version = __CURRENT_DB_VERSION__

    try:
        existing_tables = {t.lower() for t in db.get_tables()}
    except Exception as e:
        logger.warning(f"Error consultando tablas existentes: {e}")
        existing_tables = set()

    # Tablas críticas de negocio para identificar si la BD ya tiene contenido
    core_tables = {"source", "job", "payload", "telegramchat", "telegramuser"}
    has_core_tables = bool(existing_tables.intersection(core_tables))
    has_schema_version_table = "schema_version" in existing_tables

    # Caso Base de Datos Nueva (Fresh)
    if not has_core_tables and not has_schema_version_table:
        return DatabaseInspectionReport(
            state=DatabaseState.FRESH,
            current_version=0,
            target_version=target_version,
            engine_name=engine_name,
            details="Base de datos vacía, lista para inicialización.",
        )

    # Caso con Tabla `schema_version` existente
    if has_schema_version_table:
        SchemaVersion._meta.database = db
        try:
            latest = (
                SchemaVersion.select().order_by(SchemaVersion.version.desc()).first()
            )
            current_version = latest.version if latest else 0

            if current_version == target_version:
                state = DatabaseState.UP_TO_DATE
            elif current_version < target_version:
                state = DatabaseState.OUTDATED
            else:
                state = DatabaseState.AHEAD

            return DatabaseInspectionReport(
                state=state,
                current_version=current_version,
                target_version=target_version,
                engine_name=engine_name,
            )
        except Exception as e:
            return DatabaseInspectionReport(
                state=DatabaseState.CORRUPTED,
                current_version=0,
                target_version=target_version,
                engine_name=engine_name,
                details=f"Error leyendo 'schema_version': {e}",
            )

    # Caso SQLite Legacy (Usa PRAGMA user_version)
    if isinstance(db, peewee.SqliteDatabase) and has_core_tables:
        try:
            cursor = db.execute_sql("PRAGMA user_version")
            pragma_version = cursor.fetchone()[0]
            return DatabaseInspectionReport(
                state=DatabaseState.LEGACY_SQLITE,
                current_version=pragma_version,
                target_version=target_version,
                engine_name=engine_name,
                details=f"SQLite legacy detectada vía PRAGMA user_version={pragma_version}",
            )
        except Exception as e:
            return DatabaseInspectionReport(
                state=DatabaseState.CORRUPTED,
                current_version=0,
                target_version=target_version,
                engine_name=engine_name,
                details=f"Fallo leyendo PRAGMA user_version: {e}",
            )

    # Caso Inconsistente / Corrupto
    return DatabaseInspectionReport(
        state=DatabaseState.CORRUPTED,
        current_version=0,
        target_version=target_version,
        engine_name=engine_name,
        details="Se detectaron tablas pero falta el registro de versiones.",
    )


# MOTOR DE MIGRACIONES Y REGISTRO DE PASOS


def _get_migrator(db: peewee.Database):
    """Instancia el migrador DDL adecuado según el motor activo."""
    if isinstance(db, peewee.SqliteDatabase):
        return SqliteMigrator(db)
    return PostgresqlMigrator(db)


def _step_v0_to_v1(db: peewee.Database):
    """Paso histórico v0 -> v1: Agregar status y claimed_by."""
    migrator = _get_migrator(db)
    status_field = peewee.CharField(default="PENDING", null=True)
    claimed_by_field = peewee.CharField(null=True)
    try:
        migrate(
            migrator.add_column("payload", "status", status_field),
            migrator.add_column("payload", "claimed_by", claimed_by_field),
        )
    except Exception as e:
        logger.debug(f"Aviso en paso v0->v1: {e}")


def _step_v1_to_v2(db: peewee.Database):
    """Paso v1 -> v2: Eliminar status y claimed_by (desacoplamiento)."""
    migrator = _get_migrator(db)
    try:
        migrate(
            migrator.drop_column("payload", "status"),
            migrator.drop_column("payload", "claimed_by"),
        )
    except Exception as e:
        logger.debug(f"Aviso en paso v1->v2 (DROP COLUMN): {e}")


# Registro de pasos: clave representa la versión de origen (from_version)
MIGRATION_REGISTRY: Dict[int, Callable[[peewee.Database], None]] = {
    0: _step_v0_to_v1,  # Lleva de v0 a v1
    1: _step_v1_to_v2,  # Lleva de v1 a v2
}


@contextmanager
def migration_lock(db: peewee.Database):
    """
    Garantiza exclusión mutua durante la inicialización o migración de esquema.
    - PostgreSQL: Adquiere un Advisory Lock distribuido de nivel de sesión.
    - SQLite: Adquiere el semáforo en memoria para hilos locales.
    """
    is_sqlite = isinstance(db, peewee.SqliteDatabase)

    if is_sqlite:
        with _sqlite_migration_lock:
            yield
    else:
        logger.debug("Adquiriendo PostgreSQL Advisory Lock para migración...")
        db.execute_sql("SELECT pg_advisory_lock(%s);", (MIGRATION_ADVISORY_LOCK_ID,))
        try:
            yield
        finally:
            logger.debug("Liberando PostgreSQL Advisory Lock...")
            db.execute_sql(
                "SELECT pg_advisory_unlock(%s);", (MIGRATION_ADVISORY_LOCK_ID,)
            )


def _backup_sqlite_db_if_needed(db: peewee.SqliteDatabase, current_version: int):
    """Crea un archivo .bak de la base de datos SQLite antes de alterar tablas."""
    db_file_path = getattr(db, "database", None)
    if db_file_path and db_file_path != ":memory:":
        try:
            path = Path(db_file_path)
            if path.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_name = f"{path.name}.v{current_version}.{timestamp}.bak"
                backup_path = path.parent / backup_name
                shutil.copy(path, backup_path)
                logger.info(f"Backup de seguridad SQLite creado: {backup_path.name}")
        except Exception as e:
            logger.warning(f"No se pudo crear el backup de SQLite: {e}")


def _initialize_fresh_database(db: peewee.Database, target_version: int):
    """Crea todas las tablas del modelo actual y registra la versión máxima."""
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

    models: List[type[peewee.Model]] = [
        SchemaVersion,
        Source,
        Job,
        Payload,
        RemotePayload,
        TelegramChat,
        TelegramUser,
        TapeMember,
        TapeMemberGPS,
        Claim,
    ]

    with db.atomic():
        SchemaVersion._meta.database = db
        db.create_tables(models, safe=True)
        SchemaVersion.create(version=target_version)

    logger.info(f"Base de datos inicializada en la versión {target_version}.")


def _migrate_legacy_sqlite_bridge(db: peewee.SqliteDatabase, legacy_version: int):
    """Transfiere la versión de PRAGMA user_version a la tabla `schema_version`."""
    SchemaVersion._meta.database = db
    with db.atomic():
        db.create_tables([SchemaVersion], safe=True)
        SchemaVersion.create(version=legacy_version)
        db.execute_sql("PRAGMA user_version = 0;")
    logger.info(f"Base SQLite migrada al sistema 'schema_version' (v{legacy_version}).")


def _apply_pending_migrations(db: peewee.Database, from_version: int, to_version: int):
    """Ejecuta secuencialmente los pasos registrados entre la versión actual y el objetivo."""
    SchemaVersion._meta.database = db

    for v in range(from_version, to_version):
        next_v = v + 1
        step_func = MIGRATION_REGISTRY.get(v)

        with db.atomic():
            if step_func:
                logger.info(f"Ejecutando migración de esquema: v{v} -> v{next_v}...")
                step_func(db)
            else:
                logger.debug(f"Paso v{v} -> v{next_v} sin operaciones DDL requeridas.")

            SchemaVersion.create(version=next_v)
            logger.info(f"Esquema actualizado exitosamente a la versión {next_v}.")


# ORQUESTADOR PRINCIPAL


def setup_database_schema(db: peewee.Database) -> DatabaseInspectionReport:
    """
    Punto de entrada principal para preparar la base de datos.
    Inspecciona y aplica cambios solo si es estrictamente necesario bajo lock.
    """
    with migration_lock(db):
        report = inspect_database(db)

        if report.state == DatabaseState.UP_TO_DATE:
            logger.debug(
                f"Base de datos al día ({report.engine_name} v{report.current_version})."
            )
            return report

        if report.state == DatabaseState.AHEAD:
            msg = (
                f"Incompatibilidad detectada: La base de datos es versión {report.current_version}, "
                f"pero este programa solo soporta hasta la versión {report.target_version}.\n"
                "ACCIÓN: Actualiza toTelegram ejecutando: pip install --upgrade totelegram"
            )
            logger.critical(msg)
            raise IncompatibleDatabaseError(msg)

        if report.state == DatabaseState.FRESH:
            logger.info(f"Configurando base de datos nueva ({report.engine_name})...")
            _initialize_fresh_database(db, report.target_version)
            return inspect_database(db)

        if report.state == DatabaseState.LEGACY_SQLITE:
            _migrate_legacy_sqlite_bridge(db, report.current_version)  # type: ignore
            # Re-evaluar por si la versión legacy requiere upgrades adicionales
            report = inspect_database(db)
            if report.state == DatabaseState.OUTDATED:
                return setup_database_schema(db)
            return report

        if report.state == DatabaseState.OUTDATED:
            logger.info(
                f"Base de datos desactualizada (v{report.current_version} -> v{report.target_version})."
            )
            if isinstance(db, peewee.SqliteDatabase):
                _backup_sqlite_db_if_needed(db, report.current_version)

            _apply_pending_migrations(db, report.current_version, report.target_version)
            return inspect_database(db)

        if report.state == DatabaseState.CORRUPTED:
            msg = f"La base de datos se encuentra en un estado inconsistente: {report.details}"
            logger.error(msg)
            raise RuntimeError(msg)

        return report
