import enum
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import peewee

from totelegram import __CURRENT_DB_VERSION__

logger = logging.getLogger(__name__)

# Lock local en memoria para proteger SQLite contra accesos concurrentes de subprocesos
_sqlite_migration_lock = threading.RLock()


class DatabaseState(str, enum.Enum):
    """Estados del ciclo de vida y compatibilidad de la base de datos."""

    FRESH = "fresh"  # Base de datos vacía, lista para inicialización canónica
    UP_TO_DATE = "up_to_date"  # Lista para operar en la versión canónica actual
    OUTDATED = "outdated"  # Versión previa incompatible (< versión base)
    AHEAD = "ahead"  # Versión futura (bloquear ejecución)
    CORRUPTED = "corrupted"  # Tablas existentes pero esquema inconsistente


class IncompatibleDatabaseError(RuntimeError):
    """Excepción lanzada cuando la base de datos es incompatible con el cliente."""


@dataclass
class DatabaseInspectionReport:
    """Reporte detallado del diagnóstico de la base de datos."""

    state: DatabaseState
    current_version: int
    target_version: int
    engine_name: str
    details: str | None = None
    archived_legacy_path: str | None = None

    @property
    def is_operational(self) -> bool:
        return self.state == DatabaseState.UP_TO_DATE


class SchemaVersion(peewee.Model):
    """Tabla canónica y unificada para control de versiones."""

    version = peewee.IntegerField(primary_key=True)
    applied_at = peewee.DateTimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        from totelegram.database import db_proxy

        database = db_proxy
        table_name = "schema_version"


def inspect_database(db: peewee.Database) -> DatabaseInspectionReport:
    """Inspecciona la base de datos de manera estrictamente de SOLO LECTURA."""
    from totelegram.database import db_proxy

    if db_proxy.obj != db:
        db_proxy.initialize(db)

    engine_name = "sqlite"
    target_version = __CURRENT_DB_VERSION__

    try:
        existing_tables = {t.lower() for t in db.get_tables()}
    except Exception as e:
        logger.warning(f"Error consultando tablas existentes: {e}")
        existing_tables = set()

    core_tables = {"source", "job", "payload", "telegramchat", "telegramuser"}
    has_core_tables = bool(existing_tables.intersection(core_tables))
    has_schema_version_table = "schema_version" in existing_tables

    # Base de Datos Nueva (Fresh)
    if not has_core_tables and not has_schema_version_table:
        return DatabaseInspectionReport(
            state=DatabaseState.FRESH,
            current_version=0,
            target_version=target_version,
            engine_name=engine_name,
            details="Base de datos vacía, lista para inicialización canónica.",
        )

    # Tabla schema_version presente
    if has_schema_version_table:
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

    # Tablas existentes pero sin schema_version (Formato heredado no soportado)
    if has_core_tables:
        return DatabaseInspectionReport(
            state=DatabaseState.OUTDATED,
            current_version=0,
            target_version=target_version,
            engine_name=engine_name,
            details="Base de datos heredada sin control de versiones moderno.",
        )

    return DatabaseInspectionReport(
        state=DatabaseState.CORRUPTED,
        current_version=0,
        target_version=target_version,
        engine_name=engine_name,
        details="Se detectaron tablas pero el esquema es inconsistente.",
    )


@contextmanager
def migration_lock(db: peewee.Database | None = None):
    """Garantiza exclusión mutua local durante la inicialización o verificación de esquema."""
    with _sqlite_migration_lock:
        yield


def _archive_outdated_database(
    db: peewee.Database, current_version: int
) -> Path | None:
    """
    Cierra conexiones activas y renombra físicamente la base de datos antigua
    junto con sus archivos asociados (WAL/SHM), liberando el camino para una base limpia.
    """
    db_name = getattr(db, "database", None)
    if not db_name or str(db_name) == ":memory:":
        return None

    db_path = Path(str(db_name)).resolve()
    if not db_path.exists():
        return None

    # Cerrar conexiones activas para liberar locks en SQLite
    if not db.is_closed():
        db.close()

    v_tag = f"v{current_version}" if current_version > 0 else "legacy"
    base_target = db_path.with_name(f"{db_path.stem}.legacy_{v_tag}{db_path.suffix}")
    archive_dest = base_target

    if archive_dest.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_dest = db_path.with_name(
            f"{db_path.stem}.legacy_{v_tag}_{timestamp}{db_path.suffix}"
        )

    try:
        db_path.rename(archive_dest)
        logger.info(f"Base de datos antigua archivada como '{archive_dest.name}'.")

        # Mover archivos asociados de transacciones (WAL, SHM, journals) si existen
        for suffix in ["-wal", "-shm", "-journal"]:
            related_file = db_path.with_name(f"{db_path.name}{suffix}")
            if related_file.exists():
                related_dest = archive_dest.with_name(f"{archive_dest.name}{suffix}")
                try:
                    related_file.rename(related_dest)
                except Exception as e:
                    logger.warning(
                        f"No se pudo mover archivo auxiliar {related_file.name}: {e}"
                    )

        # Reabrir conexión para que apunte a un nuevo archivo limpio en la ruta original
        db.connect(reuse_if_open=True)
        return archive_dest
    except Exception as e:
        logger.error(f"Fallo al archivar la base de datos antigua '{db_path}': {e}")
        raise RuntimeError(f"No se pudo archivar la base de datos anterior: {e}") from e


def _initialize_fresh_database(db: peewee.Database, target_version: int):
    """Crea todas las tablas del modelo canónico actual y registra la versión base."""
    from totelegram.database import db_proxy
    from totelegram.models import (
        Claim,
        Job,
        Payload,
        RemotePayload,
        RemoteSnapshot,
        Source,
        TapeMember,
        TelegramChat,
        TelegramUser,
    )

    if db_proxy.obj != db:
        db_proxy.initialize(db)

    models: List[type[peewee.Model]] = [
        SchemaVersion,
        Source,
        Job,
        Payload,
        RemotePayload,
        RemoteSnapshot,
        TelegramChat,
        TelegramUser,
        TapeMember,
        Claim,
    ]

    with db.atomic():
        db.create_tables(models, safe=True)
        SchemaVersion.create(version=target_version)

    logger.info(f"Base de datos inicializada en la versión canónica {target_version}.")


def setup_database_schema(db: peewee.Database) -> DatabaseInspectionReport:
    """Punto de entrada principal para preparar la base de datos."""
    with migration_lock(db):
        report = inspect_database(db)

        if report.state == DatabaseState.UP_TO_DATE:
            logger.debug(
                f"Base de datos al día ({report.engine_name} v{report.current_version})."
            )
            return report

        if report.state == DatabaseState.FRESH:
            logger.info(
                f"Inicializando esquema canónico en {report.engine_name} (v{report.target_version})..."
            )
            _initialize_fresh_database(db, report.target_version)
            return inspect_database(db)

        if report.state == DatabaseState.OUTDATED:
            logger.info(
                f"Detectada base de datos anterior (v{report.current_version}). "
                f"Archivando para preservar datos y re-inicializando en v{report.target_version}..."
            )
            archive_path = _archive_outdated_database(db, report.current_version)
            _initialize_fresh_database(db, report.target_version)
            new_report = inspect_database(db)

            if archive_path:
                new_report.archived_legacy_path = str(archive_path)
                try:
                    from totelegram.cli.ui import UI

                    UI.warn(
                        f"Base de datos anterior respaldada en '[bold]{archive_path.name}[/]'.",
                        spacing="top",
                    )
                    UI.info(
                        f"Se ha inicializado una base de datos limpia v{report.target_version}. Puedes continuar subiendo normalmente."
                    )
                except Exception:  # noqa: S110
                    pass

            return new_report

        if report.state == DatabaseState.AHEAD:
            msg = (
                f"Incompatibilidad detectada: La base de datos es versión {report.current_version}, "
                f"pero este programa solo soporta hasta la versión {report.target_version}.\n"
                "ACCIÓN: Actualiza toTelegram ejecutando: pip install --upgrade totelegram"
            )
            logger.critical(msg)
            raise IncompatibleDatabaseError(msg)

        if report.state == DatabaseState.CORRUPTED:
            msg = f"La base de datos se encuentra en un estado inconsistente: {report.details}"
            logger.error(msg)
            raise RuntimeError(msg)

        return report
