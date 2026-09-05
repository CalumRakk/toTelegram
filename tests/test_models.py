import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock

import peewee

from totelegram.database import DatabaseSession
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.schemas import JobStatus, SourceType, Strategy, StrategyConfig


class TestModelsArchitecture(unittest.TestCase):
    def setUp(self):
        self.db_manager = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db_manager.start()

        self.chat = TelegramChat.create(
            id=-100123456, title="Test Chat", type="channel"
        )
        self.chat_alternate = TelegramChat.create(
            id=-100123801, title="Alternate Chat", type="channel"
        )
        self.user = TelegramUser.create(id=12345, first_name="Tester", is_premium=False)

    def tearDown(self):
        self.db_manager.close()

    def test_source_file_uniqueness_by_md5(self):
        """Prueba la unicidad de Source por MD5."""
        Source.create(
            path_str="video.mp4",
            md5sum="abc123unique",
            size=500,
            mtime=1.0,
            mimetype="video/mp4",
        )
        with self.assertRaises(peewee.IntegrityError):
            Source.create(
                path_str="otra_ruta/video.mp4",
                md5sum="abc123unique",
                size=500,
                mtime=1.0,
                mimetype="video/mp4",
            )

    def test_job_contract_strategy_assignment(self):
        """ADR-002: El Job determina la estrategia al nacer según los límites."""
        source = Source.create(
            path_str="data.bin",
            md5sum="hash1",
            size=150,
            mtime=1.0,
            mimetype="application/octet-stream",
        )

        # CASO 1: Archivo (150b) > Límite (100b) -> CHUNKED
        job_chunked = Job.formalize_intent(
            source, self.chat, is_premium=False, tg_limit=100
        )
        self.assertEqual(job_chunked.strategy, Strategy.CHUNKED)
        self.assertEqual(job_chunked.config.tg_max_size, 100)

        # CASO 2: Archivo (150b) < Límite (200b) -> SINGLE
        job_single = Job.formalize_intent(
            source, self.chat_alternate, is_premium=True, tg_limit=200
        )
        self.assertEqual(job_single.strategy, Strategy.SINGLE)
        self.assertEqual(job_single.config.tg_max_size, 200)

    def test_job_immutability_integrity(self):
        """Verifica que la configuración del Job queda persistida en JSON (ADR-002)."""
        source = Source.create(
            path_str="test.zip",
            md5sum="hash_imm",
            size=150,
            mtime=1.0,
            mimetype="app/zip",
        )

        job = Job.formalize_intent(source, self.chat, is_premium=False, tg_limit=100)

        job_from_db = Job.get_by_id(job.id)
        self.assertEqual(job_from_db.config.tg_max_size, 100)

    def test_job_prepare_chunks_and_payload_pending_count(self):
        """Valida que prepare_chunks particione y calcule piezas pendientes."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"x" * 250)
            tmp_path = Path(tmp.name)

        try:
            source = Source.create(
                path_str=str(tmp_path),
                md5sum="hash_file_250",
                size=250,
                mtime=1.0,
                mimetype="application/octet-stream",
                type=SourceType.FILE,
            )

            job = Job.create(
                source=source,
                chat=self.chat,
                strategy=Strategy.CHUNKED,
                status=JobStatus.PENDING,
                config=StrategyConfig(
                    tg_max_size=100, user_is_premium=False, app_version="0.9.15"
                ),
            )

            mock_settings = MagicMock(exclude_files=[])
            payloads = job.prepare_chunks(tmp_path, mock_settings)

            # 250 bytes / 100 bytes = 3 partes (100, 100, 50)
            self.assertEqual(len(payloads), 3)
            self.assertEqual(Payload.total_pending_for_job(job), 3)
            self.assertFalse(payloads[0].has_remote)

            # Subir la primera parte
            mock_msg = MagicMock(id=999, chat=MagicMock(id=self.chat.id))
            mock_msg.__str__.return_value = json.dumps(  # type: ignore
                {"id": 999, "chat": {"id": self.chat.id}}
            )

            RemotePayload.register_upload(payloads[0], mock_msg, self.user)

            self.assertEqual(Payload.total_pending_for_job(job), 2)
            self.assertTrue(Payload.get_by_id(payloads[0].id).has_remote)

        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_remote_payload_lifecycle_and_freshness(self):
        """Valida el ciclo de vida de RemotePayload (verificado, orfanado, frescura)."""
        source = Source.create(
            path_str="doc.pdf", md5sum="h_pdf", size=100, mtime=1.0, mimetype="app/pdf"
        )
        job = Job.formalize_intent(source, self.chat, False, 1000)
        payload = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=100,
            size=100,
            filename="doc.pdf",
            filename_short="doc.pdf",
        )

        # FIX: Declarar empty=False explícitamente en el mock
        mock_msg = MagicMock(id=505, chat=MagicMock(id=self.chat.id), empty=False)
        mock_msg.__str__.return_value = json.dumps(  # type: ignore
            {"id": 505, "chat": {"id": self.chat.id}}
        )

        remote = RemotePayload.register_upload(payload, mock_msg, self.user)

        # Recién creado sin verificar -> No es fresh
        self.assertFalse(remote.is_fresh)

        # Marcado como verificado ahora -> Es fresh
        remote.mark_verified(mock_msg)
        self.assertTrue(remote.is_fresh)
        self.assertFalse(remote.is_orphaned)

        # Marcado como huérfano
        remote.mark_orphaned()
        self.assertTrue(remote.is_orphaned)
        self.assertFalse(remote.is_fresh)

        # Verificación de expiración temporal (más de 15 minutos)
        remote.is_orphaned = False
        remote.last_verified_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        remote.save()
        self.assertFalse(remote.is_fresh)

    def test_job_mark_deleted_orphans_remotes(self):
        """Al marcar un Job como borrado, todos sus RemotePayload deben quedar huérfanos."""
        source = Source.create(
            path_str="del.bin", md5sum="h_del", size=10, mtime=1.0, mimetype="bin"
        )
        job = Job.formalize_intent(source, self.chat, False, 1000)
        payload = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=10,
            size=10,
            filename="del.bin",
            filename_short="del.bin",
        )

        mock_msg = MagicMock(id=777, chat=MagicMock(id=self.chat.id))
        mock_msg.__str__.return_value = json.dumps(  # type: ignore
            {"id": 777, "chat": {"id": self.chat.id}}
        )
        remote = RemotePayload.register_upload(payload, mock_msg, self.user)

        job.mark_deleted()

        job_refreshed = Job.get_by_id(job.id)
        self.assertEqual(job_refreshed.status, JobStatus.DELETED)
        self.assertGreater(job_refreshed.deleted_at, 0)

        remote_refreshed = RemotePayload.get_by_id(remote.id)
        self.assertTrue(remote_refreshed.is_orphaned)


if __name__ == "__main__":
    unittest.main()
