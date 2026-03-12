import logging
import shutil
from datetime import datetime
from pathlib import Path

import peewee

from totelegram import CURRENT_DB_VERSION

logger = logging.getLogger(__name__)


def run_migrations(db: peewee.SqliteDatabase, db_path: Path | str):
    """Gestor de migraciones lineales con backup y guardia de seguridad."""

    cursor = db.execute_sql("PRAGMA user_version")
    db_version = cursor.fetchone()[0]

    if db_version == CURRENT_DB_VERSION:
        return

    if db_version > CURRENT_DB_VERSION:
        msg = (
            f"Incompatibilidad detectada: La base de datos es versión {db_version}, "
            f"pero este programa solo soporta hasta la versión {CURRENT_DB_VERSION}.\n"
            "ACCION: Actualiza toTelegram ('pip install -U totelegram') o borra la base de datos para empezar de cero."
        )
        logger.critical(msg)
        raise RuntimeError(msg)

    if isinstance(db_path, Path):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{db_path.name}.v{db_version}.{timestamp}.bak"
        backup_path = db_path.parent / backup_name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(db_path, backup_path)
        logger.info(f"Backup de seguridad creado: {backup_path.name}")

        try:
            with db.atomic():
                if db_version < 1:
                    _migrate_to_v1(db)

                # if db_version < 2:
                #     _migrate_to_v2(db)

                db.execute_sql(f"PRAGMA user_version = {CURRENT_DB_VERSION}")
                logger.info(
                    f"Base de datos migrada con éxito a la versión {CURRENT_DB_VERSION}"
                )

        except Exception as e:
            logger.error(f"Fallo crítico en la migración: {e}")
            raise e


def _migrate_to_v1(db):
    """Migración para soporte de concurrencia."""
    try:
        logger.info("Aplicando cambios de esquema para soporte multi-worker...")

        db.execute_sql("ALTER TABLE payload ADD COLUMN status TEXT DEFAULT 'PENDING'")
        db.execute_sql("ALTER TABLE payload ADD COLUMN claimed_by TEXT")

        # Sincronizar datos: Si tiene un RemotePayload, ya está subido.
        db.execute_sql(
            """
            UPDATE payload SET status = 'UPLOADED'
            WHERE id IN (SELECT payload_id FROM remotepayload)
        """
        )
    except:
        pass
