import unittest
from datetime import datetime, timezone

from totelegram import __CURRENT_DB_VERSION__
from totelegram.database import DatabaseSession, db_proxy, sanitize_database_url
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

    def test_01_fresh_database_detection_and_initialization(self):
        """
        Base de datos recién creada (vacía).
        Debe detectarse como FRESH e inicializarse directamente en la versión objetivo.
        """
        # 1. Inspección en blanco usando nuestra conexión
        report = inspect_database(self.db)
        self.assertEqual(report.state, DatabaseState.FRESH)
        self.assertEqual(report.current_version, 0)
        self.assertEqual(report.target_version, __CURRENT_DB_VERSION__)

        # 2. Inicialización mediante el orquestador
        final_report = setup_database_schema(self.db)
        self.assertEqual(final_report.state, DatabaseState.UP_TO_DATE)
        self.assertEqual(final_report.current_version, __CURRENT_DB_VERSION__)

        # 3. Validar que la tabla schema_version tiene el registro correcto
        latest_version = (
            SchemaVersion.select().order_by(SchemaVersion.version.desc()).first()
        )
        self.assertIsNotNone(latest_version)
        self.assertEqual(latest_version.version, __CURRENT_DB_VERSION__)

    def test_02_legacy_sqlite_bridge_migration(self):
        """
        Base de datos SQLite antigua que usaba 'PRAGMA user_version = 1'.
        Debe detectarse como LEGACY_SQLITE, migrar al sistema schema_version y
        actualizarse automáticamente a la versión actual.
        """
        # 1. Simular base de datos legacy v1 con tablas existentes y PRAGMA
        self.db.create_tables([TelegramUser])
        self.db.execute_sql("PRAGMA user_version = 1;")

        report = inspect_database(self.db)
        self.assertEqual(report.state, DatabaseState.LEGACY_SQLITE)
        self.assertEqual(report.current_version, 1)

        # 2. Ejecutar orquestador
        final_report = setup_database_schema(self.db)
        self.assertEqual(final_report.state, DatabaseState.UP_TO_DATE)
        self.assertEqual(final_report.current_version, __CURRENT_DB_VERSION__)

        # 3. El PRAGMA user_version debe haberse reseteado a 0 (deprecado)
        cursor = self.db.execute_sql("PRAGMA user_version;")
        self.assertEqual(cursor.fetchone()[0], 0)

        # 4. Deben existir los registros de versión en schema_version
        versions = [
            row.version
            for row in SchemaVersion.select().order_by(SchemaVersion.version.asc())
        ]
        self.assertIn(1, versions)
        self.assertIn(__CURRENT_DB_VERSION__, versions)

    def test_03_outdated_database_incremental_migration(self):
        """
        Base de datos con schema_version en v1 cuando la app requiere v2+.
        Debe detectarse como OUTDATED y aplicar secuencialmente los pasos pendientes.
        """
        # 1. Crear schema_version con v1
        self.db.create_tables([SchemaVersion, TelegramUser])
        SchemaVersion.create(version=1, applied_at=datetime.now(timezone.utc))

        report = inspect_database(self.db)
        self.assertEqual(report.state, DatabaseState.OUTDATED)
        self.assertEqual(report.current_version, 1)

        # 2. Aplicar migraciones
        final_report = setup_database_schema(self.db)
        self.assertEqual(final_report.state, DatabaseState.UP_TO_DATE)
        self.assertEqual(final_report.current_version, __CURRENT_DB_VERSION__)

    def test_04_ahead_database_rejection(self):
        """
        Base de datos con versión superior a la soportada (ej. v99).
        Debe detectarse como AHEAD y lanzar IncompatibleDatabaseError impidiendo mutaciones.
        """
        self.db.create_tables([SchemaVersion, TelegramUser])
        SchemaVersion.create(version=99, applied_at=datetime.now(timezone.utc))

        report = inspect_database(self.db)
        self.assertEqual(report.state, DatabaseState.AHEAD)
        self.assertEqual(report.current_version, 99)

        # Debe lanzar excepción crítica de incompatibilidad
        with self.assertRaises(IncompatibleDatabaseError):
            setup_database_schema(self.db)

    def test_05_up_to_date_database_zero_ddl_idempotence(self):
        """
        Base de datos ya al día.
        Debe reportar UP_TO_DATE inmediatamente sin realizar operaciones DDL.
        """
        # Inicializar
        setup_database_schema(self.db)

        # Re-evaluar
        report = inspect_database(self.db)
        self.assertTrue(report.is_operational)
        self.assertEqual(report.state, DatabaseState.UP_TO_DATE)

        # Segunda llamada a setup_database_schema debe ser idempotente y limpia
        second_report = setup_database_schema(self.db)
        self.assertEqual(second_report.state, DatabaseState.UP_TO_DATE)

    def test_06_database_url_sanitization(self):
        """
        Seguridad: Sanitización de contraseñas en URLs de conexión para logs y UI.
        """
        raw_pg_url = "postgresql://myuser:supersecretpassword123@db.example.com:5432/totelegram_db"
        sanitized = sanitize_database_url(raw_pg_url)

        self.assertNotIn("supersecretpassword123", sanitized)
        self.assertIn("myuser:***@db.example.com:5432/totelegram_db", sanitized)

        # URLs de SQLite o sin password no deben romperse
        self.assertEqual(
            sanitize_database_url("sqlite:////var/data/app.sqlite"),
            "sqlite:////var/data/app.sqlite",
        )
        self.assertEqual(sanitize_database_url(None), "None")


if __name__ == "__main__":
    unittest.main()
