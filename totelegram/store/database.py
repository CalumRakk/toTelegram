import logging
from pathlib import Path

import peewee

from totelegram.core.setting import Settings

logger = logging.getLogger(__name__)
db_proxy = peewee.Proxy()


import logging
import os
import sys

import peewee

from totelegram.core.setting import Settings

logger = logging.getLogger(__name__)
db_proxy = peewee.Proxy()


class DatabaseSession:
    """
    Administrador de contexto para la base de datos SQLite.
    Incluye una protección para evitar escrituras accidentales en producción durante tests.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = None

    def _is_test_environment(self) -> bool:
        """Detecta si el código se está ejecutando bajo un test runner."""
        return (
            any(module in sys.modules for module in ["unittest", "pytest", "nose"])
            or "PYTEST_CURRENT_TEST" in os.environ
        )

    def _is_production_path(self, path) -> bool:
        """
        Verifica si la ruta de la base de datos apunta a la carpeta
        estándar de configuración del usuario.
        """
        from totelegram.core.registry import CONFIG_DIR

        try:
            # Compara si la base de datos está dentro de la carpeta real de config
            return CONFIG_DIR.resolve() in Path(path).resolve().parents
        except Exception:
            return False

    def __enter__(self):
        if db_proxy.obj is not None:
            return db_proxy

        db_path = self.settings.database_path

        # PROTECCIÓN ANTI-DESCUIDOS
        if self._is_test_environment():
            if self._is_production_path(db_path):
                raise RuntimeError(
                    f"\n[BLOQUEO DE SEGURIDAD] ¡Se detectó un test intentando escribir en la base de datos real!\n"
                    f"Ruta bloqueada: {db_path}\n"
                    f"Causa: El test no aisló correctamente la configuración (Settings/ProfileManager).\n"
                    f"Solución: Usa bases de datos ':memory:' o carpetas temporales en los tests."
                )

        logger.info(f"Iniciando base de datos en {db_path}")

        self.db = peewee.SqliteDatabase(
            str(db_path),
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
