import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import peewee

from totelegram import __CURRENT_DB_VERSION__
from totelegram.database import DatabaseSession
from totelegram.migration import (
    DatabaseState,
    IncompatibleDatabaseError,
    SchemaVersion,
    inspect_database,
    setup_database_schema,
)
from totelegram.models import TelegramUser


class TestDatabaseMigrationAndInspection(unittest.TestCase):
    def setUp(self):
        self.session = DatabaseSession("sqlite:///:memory:", auto_init_schema=False)
        self.db = self.session.start()

    def tearDown(self):
        self.session.close()

    def test_01_fresh_database_initialization(self):
        """
        Base de datos recién creada (vacía).
        Debe detectarse como FRESH e inicializarse directamente en la versión canónica actual.
        """
        report = inspect_database(self.db)
        self.assertEqual(report.state, DatabaseState.FRESH)
        self.assertEqual(report.current_version, 0)
        self.assertEqual(report.target_version, __CURRENT_DB_VERSION__)

        final_report = setup_database_schema(self.db)
        self.assertEqual(final_report.state, DatabaseState.UP_TO_DATE)
        self.assertEqual(final_report.current_version, __CURRENT_DB_VERSION__)

        latest_version = (
            SchemaVersion.select().order_by(SchemaVersion.version.desc()).first()
        )
        self.assertIsNotNone(latest_version)
        self.assertEqual(latest_version.version, __CURRENT_DB_VERSION__)

    def test_02_outdated_database_auto_archives_and_creates_fresh_v8(self):
        """
        Si la base de datos física es OUTDATED (ej: v1), NO debe lanzar error.
        Debe respaldarse físicamente como .legacy_v1.sqlite y crear una nueva limpia en v8.
        """
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_user.sqlite"
            file_db = peewee.SqliteDatabase(str(db_path))

            # Simular base de datos legacy v1 con SQL nativo
            file_db.connect()
            file_db.execute_sql(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at DATETIME);"
            )
            file_db.execute_sql(
                "INSERT INTO schema_version (version, applied_at) VALUES (1, '2023-01-01 00:00:00');"
            )
            file_db.execute_sql(
                "CREATE TABLE telegramuser (id INTEGER PRIMARY KEY, first_name TEXT);"
            )
            file_db.execute_sql(
                "INSERT INTO telegramuser (id, first_name) VALUES (111, 'OldUser');"
            )
            file_db.close()

            try:
                # Inspeccionar (modo solo lectura)
                file_db.connect()
                report = inspect_database(file_db)
                self.assertEqual(report.state, DatabaseState.OUTDATED)
                self.assertEqual(report.current_version, 1)

                # setup_database_schema debe auto-archivar y reiniciar en v8 sin romper
                final_report = setup_database_schema(file_db)
                self.assertEqual(final_report.state, DatabaseState.UP_TO_DATE)
                self.assertEqual(final_report.current_version, __CURRENT_DB_VERSION__)
                self.assertIsNotNone(final_report.archived_legacy_path)

                archived_path = Path(final_report.archived_legacy_path)
                self.assertTrue(archived_path.exists())
                self.assertIn("legacy_v1", archived_path.name)

                # Comprobar que la nueva base está limpia en v8
                latest = (
                    SchemaVersion.select()
                    .order_by(SchemaVersion.version.desc())
                    .first()
                )
                self.assertIsNotNone(latest)
                self.assertEqual(latest.version, __CURRENT_DB_VERSION__)

                # El usuario viejo no existe en la base limpia recién inicializada
                self.assertEqual(TelegramUser.select().count(), 0)
                file_db.close()
            finally:
                # Restaurar el proxy a la base en memoria del test
                from totelegram.database import db_proxy

                db_proxy.initialize(self.db)

    def test_03_legacy_without_schema_version_auto_archives(self):
        """
        Base de datos con tablas pero sin schema_version se cataloga OUTDATED
        y se auto-archiva como legacy.
        """
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_legacy.sqlite"
            file_db = peewee.SqliteDatabase(str(db_path))

            # Simular base de datos legacy sin schema_version con SQL nativo
            file_db.connect()
            file_db.execute_sql(
                "CREATE TABLE telegramuser (id INTEGER PRIMARY KEY, first_name TEXT);"
            )
            file_db.close()

            try:
                file_db.connect()
                report = inspect_database(file_db)
                self.assertEqual(report.state, DatabaseState.OUTDATED)

                final_report = setup_database_schema(file_db)
                self.assertEqual(final_report.state, DatabaseState.UP_TO_DATE)
                self.assertTrue(Path(final_report.archived_legacy_path).exists())
                file_db.close()
            finally:
                from totelegram.database import db_proxy

                db_proxy.initialize(self.db)

    def test_04_ahead_database_rejection(self):
        """
        Base de datos con versión superior a la soportada por el cliente (ej. v99).
        DEBE detectarse como AHEAD y lanzar IncompatibleDatabaseError para proteger datos futuros.
        """
        self.db.create_tables([SchemaVersion, TelegramUser])
        SchemaVersion.create(version=99, applied_at=datetime.now(timezone.utc))

        report = inspect_database(self.db)
        self.assertEqual(report.state, DatabaseState.AHEAD)
        self.assertEqual(report.current_version, 99)

        with self.assertRaises(IncompatibleDatabaseError):
            setup_database_schema(self.db)

    def test_05_up_to_date_database_zero_ddl_idempotence(self):
        """
        Base de datos al día. Idempotencia y reporte directo.
        """
        setup_database_schema(self.db)

        report = inspect_database(self.db)
        self.assertTrue(report.is_operational)
        self.assertEqual(report.state, DatabaseState.UP_TO_DATE)

        second_report = setup_database_schema(self.db)
        self.assertEqual(second_report.state, DatabaseState.UP_TO_DATE)
