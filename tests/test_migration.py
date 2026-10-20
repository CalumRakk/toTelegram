import unittest
from datetime import datetime, timezone

from totelegram import __CURRENT_DB_VERSION__
from totelegram.database import DatabaseSession, sanitize_database_url
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
        # Inspección en blanco usando nuestra conexión
        report = inspect_database(self.db)
        self.assertEqual(report.state, DatabaseState.FRESH)
        self.assertEqual(report.current_version, 0)
        self.assertEqual(report.target_version, __CURRENT_DB_VERSION__)

        # Inicialización mediante el orquestador
        final_report = setup_database_schema(self.db)
        self.assertEqual(final_report.state, DatabaseState.UP_TO_DATE)
        self.assertEqual(final_report.current_version, __CURRENT_DB_VERSION__)

        # Validar que la tabla schema_version tiene el registro correcto
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
        # Simular base de datos legacy v1 con tablas existentes y PRAGMA
        self.db.create_tables([TelegramUser])
        self.db.execute_sql("PRAGMA user_version = 1;")

        report = inspect_database(self.db)
        self.assertEqual(report.state, DatabaseState.LEGACY_SQLITE)
        self.assertEqual(report.current_version, 1)

        # Ejecutar orquestador
        final_report = setup_database_schema(self.db)
        self.assertEqual(final_report.state, DatabaseState.UP_TO_DATE)
        self.assertEqual(final_report.current_version, __CURRENT_DB_VERSION__)

        # El PRAGMA user_version debe haberse reseteado a 0 (deprecado)
        cursor = self.db.execute_sql("PRAGMA user_version;")
        self.assertEqual(cursor.fetchone()[0], 0)

        # Deben existir los registros de versión en schema_version
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
        # Crear schema_version con v1
        self.db.create_tables([SchemaVersion, TelegramUser])
        SchemaVersion.create(version=1, applied_at=datetime.now(timezone.utc))

        report = inspect_database(self.db)
        self.assertEqual(report.state, DatabaseState.OUTDATED)
        self.assertEqual(report.current_version, 1)

        # Aplicar migraciones
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

    def test_07_migration_v6_to_v7_backfill_and_drop(self):
        """
        Migración v6 -> v7:
        Verifica que los registros de 'tapemember' con start_offset en 0 sean sanados
        usando los datos de 'tapemembergps' antes de que la tabla sea eliminada.
        """
        # 1. Simular esquema v6 creando las tablas requeridas
        from totelegram.models import Job, Payload, Source, TelegramChat

        self.db.create_tables([SchemaVersion, Source, Job, Payload, TelegramChat])

        self.db.execute_sql("""
            CREATE TABLE IF NOT EXISTS tapemember (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                relative_path TEXT,
                size INTEGER,
                md5sum TEXT,
                start_offset INTEGER DEFAULT 0
            );
        """)
        self.db.execute_sql("""
            CREATE TABLE IF NOT EXISTS tapemembergps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id INTEGER,
                payload_id INTEGER,
                state TEXT,
                offset_in_volume INTEGER,
                bytes_in_volume INTEGER,
                source_offset INTEGER DEFAULT 0
            );
        """)
        SchemaVersion.create(version=6)

        # 2. Insertar fixtures simulando datos antiguos desactualizados
        chat = TelegramChat.create(id=-1001, title="Test", type="channel")
        source = Source.create(
            path_str="/tmp/test",
            md5sum="test_md5",
            size=2000,
            mtime=1.0,
            mimetype="application/x-tar",
        )
        job = Job.formalize_intent(source, chat, False, 1000)
        p1 = Payload.create(
            job=job,
            sequence_index=1,
            start_offset=1000,
            end_offset=2000,
            size=1000,
            filename="part.02",
            filename_short="p2",
        )

        # Miembro con start_offset en 0 (típico de v6 antes de poblar)
        self.db.execute_sql(
            "INSERT INTO tapemember (id, source_id, relative_path, size, md5sum, start_offset) "
            "VALUES (10, ?, 'video.mp4', 800, 'hash1', 0);",
            (source.id,),
        )
        # Fragmento en GPS: reside en Payload p1 (offset 1000) en el byte 100 del volumen
        # calculated_offset = 1000 + 100 - 512 = 588
        self.db.execute_sql(
            "INSERT INTO tapemembergps (member_id, payload_id, state, offset_in_volume, bytes_in_volume, source_offset) "
            "VALUES (10, ?, 'COMPLETE', 100, 800, 0);",
            (p1.id,),
        )

        # 3. Ejecutar setup_database_schema para disparar v6 -> v7
        final_report = setup_database_schema(self.db)
        self.assertEqual(final_report.current_version, 7)

        # 4. Validar que start_offset fue sanado
        cursor = self.db.execute_sql(
            "SELECT start_offset FROM tapemember WHERE id = 10;"
        )
        self.assertEqual(cursor.fetchone()[0], 588)

        # 5. Validar que tapemembergps ya no existe
        tables = [t.lower() for t in self.db.get_tables()]
        self.assertNotIn("tapemembergps", tables)


if __name__ == "__main__":
    unittest.main()
