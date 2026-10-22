import unittest
from datetime import datetime, timezone

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

        # Inicialización canónica
        final_report = setup_database_schema(self.db)
        self.assertEqual(final_report.state, DatabaseState.UP_TO_DATE)
        self.assertEqual(final_report.current_version, __CURRENT_DB_VERSION__)

        # Verificar que la tabla schema_version tiene el registro correcto
        latest_version = (
            SchemaVersion.select().order_by(SchemaVersion.version.desc()).first()
        )
        self.assertIsNotNone(latest_version)
        self.assertEqual(latest_version.version, __CURRENT_DB_VERSION__)

    def test_02_outdated_database_rejection(self):
        """
        Base de datos con versión previa a la línea base canónica (ej. v1 a v7).
        Debe detectarse como OUTDATED y lanzar IncompatibleDatabaseError instruyendo
        al usuario a migrar sus datos con la herramienta dedicada.
        """
        self.db.create_tables([SchemaVersion, TelegramUser])
        SchemaVersion.create(version=1, applied_at=datetime.now(timezone.utc))

        report = inspect_database(self.db)
        self.assertEqual(report.state, DatabaseState.OUTDATED)
        self.assertEqual(report.current_version, 1)

        with self.assertRaises(IncompatibleDatabaseError):
            setup_database_schema(self.db)

    def test_03_legacy_tables_without_schema_version_rejected(self):
        """
        Base de datos con tablas pero sin la tabla schema_version.
        Debe catalogarse como OUTDATED y rechazar la ejecución.
        """
        self.db.create_tables([TelegramUser])

        report = inspect_database(self.db)
        self.assertEqual(report.state, DatabaseState.OUTDATED)

        with self.assertRaises(IncompatibleDatabaseError):
            setup_database_schema(self.db)

    def test_04_ahead_database_rejection(self):
        """
        Base de datos con versión superior a la soportada por el cliente (ej. v99).
        Debe detectarse como AHEAD y lanzar IncompatibleDatabaseError.
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
        Base de datos al día.
        Debe reportar UP_TO_DATE de inmediato sin alterar el esquema.
        """
        setup_database_schema(self.db)

        report = inspect_database(self.db)
        self.assertTrue(report.is_operational)
        self.assertEqual(report.state, DatabaseState.UP_TO_DATE)

        # La segunda ejecución debe ser idempotente y limpia
        second_report = setup_database_schema(self.db)
        self.assertEqual(second_report.state, DatabaseState.UP_TO_DATE)
