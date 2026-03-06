import unittest

import peewee

from totelegram.common.enums import Strategy
from totelegram.manager.database import DatabaseSession  # type: ignore
from totelegram.manager.models import (
    Job,
    Source,
    TelegramChat,
)


class TestModelsArchitecture(unittest.TestCase):
    def setUp(self):
        self.db_manager = DatabaseSession(":memory:")
        self.db_manager.start()
        self.chat = TelegramChat.create(
            id=-100123456, title="Test Chat", type="channel"
        )
        self.chat_alternate = TelegramChat.create(
            id=-100123801, title="Test Chat", type="channel"
        )

    def tearDown(self):
        self.db_manager.close()

    def test_source_file_uniqueness_by_md5(self):
        """Prueba la unicidad de Source por MD5."""
        Source.create(
            path_str="video.mp4",
            md5sum="abc123",
            size=500,
            mtime=1.0,
            mimetype="video/mp4",
        )
        with self.assertRaises(peewee.IntegrityError):
            Source.create(
                path_str="otra_ruta/video.mp4",
                md5sum="abc123",
                size=500,
                mtime=1.0,
                mimetype="video/mp4",
            )

    def test_job_contract_strategy_assignment(self):
        """
        Prueba la lógica de ADR-002:
        El Job determina la estrategia al nacer basado en el límite de bytes.
        """
        source = Source.create(
            path_str="data.bin",
            md5sum="hash1",
            size=150,
            mtime=1.0,
            mimetype="application/octet-stream",
        )

        # CASO : El archivo (150b) es mayor que el limite (100b) -> CHUNKED

        job_chunked = Job.formalize_intent(
            source, self.chat, is_premium=False, tg_limit=100
        )
        self.assertEqual(job_chunked.strategy, Strategy.CHUNKED)
        self.assertEqual(job_chunked.config.tg_max_size, 100)

        # CASO : El archivo (150b) es menor que el limite (200b) -> SINGLE
        job_single = Job.formalize_intent(
            source, self.chat_alternate, is_premium=True, tg_limit=200
        )
        self.assertEqual(job_single.strategy, Strategy.SINGLE)
        self.assertEqual(job_single.config.tg_max_size, 200)

    def test_job_immutability_integrity(self):
        """
        Verifica que una vez creado el Job, su config queda persistida en JSON
        y no cambia aunque cambien los parámetros externos (ADR-002).
        """
        source = Source.create(
            path_str="test.zip",
            md5sum="hash_imm",
            size=150,
            mtime=1.0,
            mimetype="app/zip",
        )

        job = Job.formalize_intent(source, self.chat, is_premium=False, tg_limit=100)

        # Recuperamos de la DB para asegurar que el JSONField funcionó
        job_from_db = Job.get_by_id(job.id)
        self.assertEqual(job_from_db.config.tg_max_size, 100)

    # def test_payload_relation_and_status(self):
    #     """Valida que los payloads se vinculen correctamente y el Job cambie de estado."""
    #     source = Source.create(
    #         path_str="doc.pdf", md5sum="h_pdf", size=10, mtime=1.0, mimetype="app/pdf"
    #     )

    #     job = Job.formalize_intent(source, self.chat, is_premium=False, tg_limit=100)

    #     self.assertEqual(job.status, JobStatus.PENDING)

    #     # Simular creación de un payload (SINGLE)
    #     Payload.create_payloads(job, [Path("doc.pdf")])
    #     self.assertEqual(job.payloads.count(), 1)

    #     job.set_uploaded()
    #     self.assertEqual(job.status, JobStatus.UPLOADED)

    # def test_remote_payload_register_upload(self):
    #     """Valida el registro del 'Vínculo de Acceso' (RemotePayload)."""
    #     source = Source.create(
    #         path_str="a.txt", md5sum="h1", size=10, mtime=1, mimetype="t"
    #     )

    #     job = Job.formalize_intent(source, self.chat, False, 100)
    #     payload = Payload.create(job=job, sequence_index=0, md5sum="hp1", size=10)

    #     user = TelegramUser.create(id=123, first_name="Tester")

    #     # Mock de objeto Message de Pyrogram que cumpla con json.loads(str(msg))
    #     class MockTgMessage:
    #         def __init__(self):
    #             self.id = 999
    #             self.chat = type("obj", (object,), {"id": -100123456})

    #         def __str__(self):
    #             return json.dumps(
    #                 {
    #                     "message_id": self.id,
    #                     "chat": {"id": self.chat.id, "type": "channel"},  # type: ignore
    #                 }
    #             )

    #     remote = RemotePayload.register_upload(
    #         payload=payload, tg_message=MockTgMessage(), owner=user
    #     )

    #     self.assertEqual(remote.message_id, 999)
    #     self.assertEqual(remote.chat_id, -100123456)
    #     self.assertEqual(remote.owner.id, 123)
