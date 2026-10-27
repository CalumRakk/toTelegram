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
    RemoteSnapshot,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.schemas import SourceType


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
        """ADR-002: El Job fija el chunk_size como contrato inmutable y calcula is_chunked."""
        source = Source.create(
            path_str="data.bin",
            md5sum="hash1",
            size=150,
            mtime=1.0,
            mimetype="application/octet-stream",
        )

        job_chunked = Job.formalize_intent(source, self.chat, chunk_size=100)
        self.assertTrue(job_chunked.is_chunked)
        self.assertEqual(job_chunked.chunk_size, 100)
        self.assertEqual(job_chunked.total_expected_parts, 2)

        job_single = Job.formalize_intent(source, self.chat_alternate, chunk_size=200)
        self.assertFalse(job_single.is_chunked)
        self.assertEqual(job_single.chunk_size, 200)
        self.assertEqual(job_single.total_expected_parts, 1)

    def test_job_immutability_integrity(self):
        """Verifica que el contrato físico de partición (chunk_size) queda persistido (ADR-002)."""
        source = Source.create(
            path_str="test.zip",
            md5sum="hash_imm",
            size=150,
            mtime=1.0,
            mimetype="app/zip",
        )

        job = Job.formalize_intent(source, self.chat, chunk_size=100)
        job_from_db = Job.get_by_id(job.id)
        self.assertEqual(job_from_db.chunk_size, 100)

    def test_job_derived_completion_state(self):
        """Valida que is_completed_local se derive puramente de las piezas subidas."""
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
                chunk_size=100,
            )

            mock_settings = MagicMock(exclude_files=[])
            payloads = job.prepare_chunks(tmp_path, mock_settings)

            # 3 partes generadas, 0 subidas -> No está completado
            self.assertEqual(len(payloads), 3)
            self.assertFalse(job.is_completed_local)
            self.assertFalse(job.has_remote_snapshot)

            # Subir parte 1 de 3 -> Sigue incompleto
            mock_msg1 = MagicMock(id=991, chat=MagicMock(id=self.chat.id), empty=False)
            mock_msg1.__str__.return_value = json.dumps(
                {"id": 991, "chat": {"id": self.chat.id}}
            )
            RemotePayload.register_upload(payloads[0], mock_msg1, self.user)
            self.assertFalse(job.is_completed_local)

            # Subir partes 2 y 3 -> Ahora es completado localmente sin flags almacenados
            mock_msg2 = MagicMock(id=992, chat=MagicMock(id=self.chat.id), empty=False)
            mock_msg2.__str__.return_value = json.dumps(
                {"id": 992, "chat": {"id": self.chat.id}}
            )
            RemotePayload.register_upload(payloads[1], mock_msg2, self.user)

            mock_msg3 = MagicMock(id=993, chat=MagicMock(id=self.chat.id), empty=False)
            mock_msg3.__str__.return_value = json.dumps(
                {"id": 993, "chat": {"id": self.chat.id}}
            )
            RemotePayload.register_upload(payloads[2], mock_msg3, self.user)

            self.assertTrue(job.is_completed_local)

        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_remote_payload_lifecycle_and_freshness(self):
        """Valida el ciclo de vida de RemotePayload (verificado, orfanado, frescura)."""
        source = Source.create(
            path_str="doc.pdf", md5sum="h_pdf", size=100, mtime=1.0, mimetype="app/pdf"
        )
        job = Job.formalize_intent(source, self.chat, 1000)
        payload = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=100,
            size=100,
            filename="doc.pdf",
            filename_short="doc.pdf",
        )

        mock_msg = MagicMock(id=505, chat=MagicMock(id=self.chat.id), empty=False)
        mock_msg.__str__.return_value = json.dumps(
            {"id": 505, "chat": {"id": self.chat.id}}
        )

        remote = RemotePayload.register_upload(payload, mock_msg, self.user)

        self.assertFalse(remote.is_fresh)
        remote.mark_verified(mock_msg)
        self.assertTrue(remote.is_fresh)
        self.assertFalse(remote.is_orphaned)

        remote.mark_orphaned()
        self.assertTrue(remote.is_orphaned)
        self.assertFalse(remote.is_fresh)

        remote.is_orphaned = False
        remote.last_verified_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        remote.save()
        self.assertFalse(remote.is_fresh)

    def test_job_purge_cascades_cleanly(self):
        """Al purgar un Job, se eliminan en cascada sus Payloads, Remotes y Snapshots sin violar FKs."""
        source = Source.create(
            path_str="del.bin", md5sum="h_del_clean", size=10, mtime=1.0, mimetype="bin"
        )
        job = Job.formalize_intent(source, self.chat, 1000)
        payload = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=10,
            size=10,
            filename="del.bin",
            filename_short="del.bin",
        )

        mock_msg = MagicMock(id=777, chat=MagicMock(id=self.chat.id), empty=False)
        mock_msg.__str__.return_value = json.dumps(
            {"id": 777, "chat": {"id": self.chat.id}}
        )
        remote_payload = RemotePayload.register_upload(payload, mock_msg, self.user)
        remote_snapshot = RemoteSnapshot.register(job, 888, self.chat, self.user)

        job_id = job.id
        payload_id = payload.id
        remote_p_id = remote_payload.id
        remote_s_id = remote_snapshot.id

        # Ejecutamos la purga atómica
        job.purge()

        # Verificar que absolutamente todo fue eliminado físicamente
        self.assertIsNone(Job.get_or_none(Job.id == job_id))
        self.assertIsNone(Payload.get_or_none(Payload.id == payload_id))
        self.assertIsNone(RemotePayload.get_or_none(RemotePayload.id == remote_p_id))
        self.assertIsNone(RemoteSnapshot.get_or_none(RemoteSnapshot.id == remote_s_id))

    def test_multiple_completed_jobs_coexistence(self):
        """Valida que puedan convivir múltiples Jobs legítimos para el mismo Source y Chat."""
        source = Source.create(
            path_str="multijob.mkv",
            md5sum="hash_multijob_coexistence",
            size=4000,
            mtime=1.0,
            mimetype="video/x-matroska",
        )

        # Job 1: Subida con bloques de 2000 bytes
        job_1 = Job.create(source=source, chat=self.chat, chunk_size=2000)
        p1 = Payload.create(
            job=job_1,
            sequence_index=0,
            start_offset=0,
            end_offset=2000,
            size=2000,
            filename="multijob.mkv.01-02",
            filename_short="m.mkv.01-02",
        )
        p2 = Payload.create(
            job=job_1,
            sequence_index=1,
            start_offset=2000,
            end_offset=4000,
            size=2000,
            filename="multijob.mkv.02-02",
            filename_short="m.mkv.02-02",
        )
        msg1 = MagicMock(id=101, chat=MagicMock(id=self.chat.id), empty=False)
        msg1.__str__.return_value = json.dumps(
            {"id": 101, "chat": {"id": self.chat.id}}
        )
        RemotePayload.register_upload(p1, msg1, self.user)
        RemotePayload.register_upload(p2, msg1, self.user)
        self.assertTrue(job_1.is_completed_local)

        # Job 2: Subida posterior con bloques de 4000 bytes (Premium) para el mismo chat
        job_2 = Job.create(source=source, chat=self.chat, chunk_size=4000)
        p3 = Payload.create(
            job=job_2,
            sequence_index=0,
            start_offset=0,
            end_offset=4000,
            size=4000,
            filename="multijob.mkv",
            filename_short="m.mkv",
        )
        RemotePayload.register_upload(p3, msg1, self.user)
        self.assertTrue(job_2.is_completed_local)

        # Ambos Jobs deben coexistir sin colisionar
        completed = Job.get_completed_for_source_in_chat(source, self.chat)
        self.assertEqual(len(completed), 2)

    def test_remote_snapshot_lifecycle_and_job_properties(self):
        """Valida que RemoteSnapshot se registre y Job exponga sus propiedades dinámicamente."""
        source = Source.create(
            path_str="backup.tar",
            md5sum="hash_snap_model_test",
            size=1000,
            mtime=1.0,
            mimetype="application/x-tar",
        )
        job = Job.formalize_intent(source, self.chat, 2000)

        self.assertIsNone(job.remote_snapshot)
        self.assertIsNone(job.snapshot_message_id)
        self.assertFalse(job.has_remote_snapshot)

        remote_snap = RemoteSnapshot.register(
            job=job,
            message_id=9999,
            chat=self.chat,
            owner=self.user,
        )

        self.assertIsNotNone(job.remote_snapshot)
        self.assertEqual(job.remote_snapshot.id, remote_snap.id)
        self.assertEqual(job.snapshot_message_id, 9999)
        self.assertTrue(job.has_remote_snapshot)
        self.assertTrue(remote_snap.is_fresh)
        self.assertFalse(remote_snap.is_orphaned)

        remote_snap.mark_orphaned()
        self.assertIsNone(job.remote_snapshot)
        self.assertIsNone(job.snapshot_message_id)
        self.assertFalse(job.has_remote_snapshot)


if __name__ == "__main__":
    unittest.main()
