import unittest
from pathlib import Path

from peewee import SqliteDatabase

from totelegram.core.enums import JobStatus, Strategy
from totelegram.core.setting import Settings
from totelegram.store.models import (
    Job,
    Payload,
    RemotePayload,
    SourceFile,
    TelegramChat,
    TelegramUser,
    db_proxy,
)


class TestModelsArchitecture(unittest.TestCase):
    def setUp(self):
        self.test_db = SqliteDatabase(":memory:")
        db_proxy.initialize(self.test_db)
        db_proxy.create_tables(
            [SourceFile, Job, TelegramChat, Payload, RemotePayload, TelegramUser]
        )

        # Configuración mínima de prueba
        self.settings = Settings(
            profile_name="test_profile",
            chat_id="-100123456",
            api_id=12345,
            api_hash="fake_hash",
            TG_MAX_SIZE_NORMAL=100,
            TG_MAX_SIZE_PREMIUM=200,
        )

        self.chat = TelegramChat.create(
            id=-100123456, title="Test Chat", type="channel"
        )
        self.chat_alternate = TelegramChat.create(
            id=-100123801, title="Test Chat", type="channel"
        )

    def tearDown(self):
        self.test_db.close()

    def test_source_file_uniqueness_by_md5(self):
        """Validar que el MD5 es el ancla de identidad (SourceFile)."""
        path = Path("video.mp4")
        # Creamos dos entradas con distintas rutas pero mismo MD5
        sf1 = SourceFile.create(
            path_str=str(path),
            md5sum="abc123",
            size=500,
            mtime=1.0,
            mimetype="video/mp4",
        )

        # Intentar crear otro con mismo MD5 debería fallar por integridad de DB
        with self.assertRaises(Exception):
            SourceFile.create(
                path_str="otra_ruta/video.mp4",
                md5sum="abc123",
                size=500,
                mtime=1.0,
                mimetype="video/mp4",
            )

    def test_job_contract_strategy_assignment(self):
        """
        Prueba la lógica de ADR-002:
        El Job determina la estrategia al nacer basado en el estado Premium.
        """
        # Para un archivo de 150 bytes
        # La estategia será CHUNKED si el usuario es normal.
        # Para premium será SINGLEsi el usuario es premium.

        source = SourceFile.create(
            path_str="data.bin",
            md5sum="hash1",
            size=150,
            mtime=1.0,
            mimetype="application/octet-stream",
        )

        # CASO A: Usuario Normal
        job_normal = Job.create_contract(
            source, self.chat, is_premium=False, settings=self.settings
        )
        self.assertEqual(job_normal.strategy, Strategy.CHUNKED)
        self.assertEqual(job_normal.config.tg_max_size, 100)

        # CASO B: Usuario Premium
        job_premium = Job.create_contract(
            source, self.chat_alternate, is_premium=True, settings=self.settings
        )
        self.assertEqual(job_premium.strategy, Strategy.SINGLE)
        self.assertEqual(job_premium.config.tg_max_size, 200)

    def test_job_immutability_integrity(self):
        """
        Verifica que una vez creado el Job, los cambios en Settings
        no afectan al contrato guardado (ADR-002).
        """
        source = SourceFile.create(
            path_str="test.zip",
            md5sum="hash_imm",
            size=150,
            mtime=1.0,
            mimetype="app/zip",
        )

        job = Job.create_contract(
            source, self.chat, is_premium=False, settings=self.settings
        )
        original_limit = job.config.tg_max_size  # 100

        # Simulamos que el usuario cambia sus settings globales a 500MB
        self.settings.TG_MAX_SIZE_NORMAL = 500_000_000

        # El Job debe mantener su contrato original de la DB
        job_from_db = Job.get_by_id(job.id)
        self.assertEqual(job_from_db.config.tg_max_size, original_limit)
        self.assertEqual(job_from_db.strategy, Strategy.CHUNKED)

    def test_payload_relation_and_status(self):
        """Valida que los payloads se vinculen correctamente y el Job cambie de estado."""
        source = SourceFile.create(
            path_str="doc.pdf", md5sum="h_pdf", size=10, mtime=1.0, mimetype="app/pdf"
        )
        job = Job.create_contract(
            source, self.chat, is_premium=False, settings=self.settings
        )

        self.assertEqual(job.status, JobStatus.PENDING)

        Payload.create_payloads(job, [Path("fake_part1.bin")])
        self.assertEqual(job.payloads.count(), 1)

        job.set_uploaded()
        self.assertEqual(job.status, JobStatus.UPLOADED)

    def test_remote_payload_access_effective(self):
        """Valida el registro del 'Vínculo de Acceso' (RemotePayload)."""
        source = SourceFile.create(
            path_str="a.txt", md5sum="h1", size=10, mtime=1, mimetype="t"
        )
        job = Job.create_contract(source, self.chat, False, self.settings)
        payload = Payload.create(job=job, sequence_index=0, md5sum="hp1", size=10)

        user = TelegramUser.create(id=123, first_name="Tester")

        # Simulación de objeto Message de Pyrogram (mínimo requerido)
        class MockTgMessage:
            def __init__(self):
                self.id = 999
                self.chat = type("obj", (object,), {"id": -100123456})

            def __str__(self):
                return (
                    '{"message_id": 999, "chat": {"id": -100123456, "type": "channel"}}'
                )

        remote = RemotePayload.register_upload(
            payload=payload, tg_message=MockTgMessage(), owner=user
        )

        self.assertEqual(remote.message_id, 999)
        self.assertEqual(remote.owner.first_name, "Tester")
        self.assertIn("message_id", remote.json_metadata)


if __name__ == "__main__":
    unittest.main()
