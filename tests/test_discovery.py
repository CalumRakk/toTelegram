import json
import unittest
from unittest.mock import MagicMock, patch

from totelegram.database import DatabaseSession
from totelegram.discovery import DiscoveryService
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    RemoteSnapshot,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.schemas import ExecutionDecision, SourceType


class TestDiscoveryService(unittest.TestCase):
    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.mock_client = MagicMock()
        self.discovery = DiscoveryService(self.mock_client, self.db)

        self.chat_target = TelegramChat.create(
            id=-100111111, title="Chat Destino", type="channel"
        )
        self.user = TelegramUser.create(
            id=12345, first_name="DiscoveryUser", is_premium=False
        )

        self.source = Source.create(
            path_str="/tmp/video_discovery.mp4",
            md5sum="hash_discovery_123",
            size=2000,
            mtime=1700000000.0,
            mimetype="video/mp4",
            type=SourceType.FILE,
        )

        self.job_target = Job.create(
            source=self.source,
            chat=self.chat_target,
            chunk_size=1000,
        )

        self.p0 = Payload.create(
            job=self.job_target,
            sequence_index=0,
            start_offset=0,
            end_offset=1000,
            size=1000,
            filename="video.mp4.01-02",
            filename_short="h.mp4.01-02",
        )
        self.p1 = Payload.create(
            job=self.job_target,
            sequence_index=1,
            start_offset=1000,
            end_offset=2000,
            size=1000,
            filename="video.mp4.02-02",
            filename_short="h.mp4.02-02",
        )

    def tearDown(self):
        self.db_session.close()

    def _create_mock_tg_message(self, message_id: int, chat_id: int, file_size: int):
        msg = MagicMock()
        msg.id = message_id
        msg.chat = MagicMock(id=chat_id)
        msg.empty = False
        msg.document = MagicMock(file_size=file_size, file_id=f"file_id_{message_id}")
        msg.__str__.return_value = json.dumps(
            {
                "id": message_id,
                "chat": {"id": chat_id},
                "document": {
                    "file_size": file_size,
                    "file_id": f"file_id_{message_id}",
                },
            }
        )
        return msg

    def test_investigate_returns_clean_restart_when_no_prior_uploads(self):
        """Si el archivo no existe en el destino, requiere subida limpia."""
        report = self.discovery.investigate(self.job_target)
        self.assertEqual(report.decision, ExecutionDecision.CLEAN_RESTART)
        self.assertEqual(len(report.valid_remotes), 0)

    def test_investigate_returns_already_fulfilled_when_all_parts_and_snapshot_exist(
        self,
    ):
        """Si todas las piezas y el snapshot existen en el destino, retorna ALREADY_FULFILLED."""
        msg0 = self._create_mock_tg_message(101, self.chat_target.id, 1000)
        msg1 = self._create_mock_tg_message(102, self.chat_target.id, 1000)

        r0 = RemotePayload.register_upload(self.p0, msg0, self.user)
        r1 = RemotePayload.register_upload(self.p1, msg1, self.user)
        r0.mark_verified(msg0)
        r1.mark_verified(msg1)

        RemoteSnapshot.register(self.job_target, 999, self.chat_target, self.user)

        report = self.discovery.investigate(self.job_target)
        self.assertEqual(report.decision, ExecutionDecision.ALREADY_FULFILLED)

    def test_investigate_returns_seal_healed_when_all_parts_exist_without_snapshot(
        self,
    ):
        """Si todas las piezas (2/2) están subidas pero falta el snapshot, devuelve SEAL_HEALED."""
        msg0 = self._create_mock_tg_message(101, self.chat_target.id, 1000)
        msg1 = self._create_mock_tg_message(102, self.chat_target.id, 1000)

        self.mock_client.get_messages.return_value = [msg0, msg1]

        RemotePayload.register_upload(self.p0, msg0, self.user)
        RemotePayload.register_upload(self.p1, msg1, self.user)

        with patch("time.sleep", return_value=None):
            report = self.discovery.investigate(self.job_target)

        self.assertEqual(report.decision, ExecutionDecision.SEAL_HEALED)
        self.assertEqual(len(report.valid_remotes), 2)

    def test_investigate_returns_resumed_in_place_when_partial_no_force(self):
        """Subida a medias con corte compatible sin --force devuelve RESUMED_IN_PLACE."""
        msg0 = self._create_mock_tg_message(101, self.chat_target.id, 1000)
        self.mock_client.get_messages.return_value = [msg0]

        RemotePayload.register_upload(self.p0, msg0, self.user)

        with patch("time.sleep", return_value=None):
            report = self.discovery.investigate(self.job_target, force=False)

        self.assertEqual(report.decision, ExecutionDecision.RESUMED_IN_PLACE)
        self.assertEqual(len(report.valid_remotes), 1)

    def test_investigate_returns_relocated_and_resumed_when_partial_with_force(self):
        """Subida a medias con corte compatible con --force devuelve RELOCATED_AND_RESUMED."""
        msg0 = self._create_mock_tg_message(101, self.chat_target.id, 1000)
        self.mock_client.get_messages.return_value = [msg0]

        RemotePayload.register_upload(self.p0, msg0, self.user)

        # Simulamos que la cuenta tiene permiso de eliminación en el canal
        with (
            patch.object(
                self.discovery.access_service, "can_delete_messages", return_value=True
            ),
            patch("time.sleep", return_value=None),
        ):
            report = self.discovery.investigate(self.job_target, force=True)

        self.assertEqual(report.decision, ExecutionDecision.RELOCATED_AND_RESUMED)
        self.assertEqual(len(report.valid_remotes), 1)

    def test_investigate_returns_incompatible_capacity_when_chunk_exceeds_limit(self):
        """Si el corte previo supera el límite de la cuenta activa, devuelve INCOMPATIBLE_CAPACITY."""
        msg0 = self._create_mock_tg_message(101, self.chat_target.id, 1000)
        self.mock_client.get_messages.return_value = [msg0]

        RemotePayload.register_upload(self.p0, msg0, self.user)

        with patch("time.sleep", return_value=None):
            # La cuenta solo puede 500 bytes, pero el Job pide 1000
            report = self.discovery.investigate(
                self.job_target, force=False, account_max_chunk_size=500
            )

        self.assertEqual(report.decision, ExecutionDecision.INCOMPATIBLE_CAPACITY)

    def test_jit_batch_marks_orphaned_when_message_deleted_in_telegram(self):
        """La validación JIT orfana el registro si Telegram informa que el mensaje fue eliminado."""
        msg0 = self._create_mock_tg_message(301, self.chat_target.id, 1000)
        r0 = RemotePayload.register_upload(self.p0, msg0, self.user)

        deleted_msg = MagicMock(id=301, empty=True, document=None)
        self.mock_client.get_messages.return_value = [deleted_msg]

        with patch("time.sleep", return_value=None):
            valid, orphaned = self.discovery._validate_jit_batch([r0])

        self.assertEqual(len(valid), 0)
        self.assertEqual(len(orphaned), 1)
        r0_refreshed = RemotePayload.get_by_id(r0.id)
        self.assertTrue(r0_refreshed.is_orphaned)

    def test_partial_resume_orphans_deleted_chunks(self):
        """Si un Job incompleto tiene piezas borradas en Telegram, JIT las orfana y pide subida limpia."""
        msg0 = self._create_mock_tg_message(901, self.chat_target.id, 1000)
        r0 = RemotePayload.register_upload(self.p0, msg0, self.user)

        deleted_msg = MagicMock(id=901, empty=True, document=None)
        self.mock_client.get_messages.return_value = [deleted_msg]

        with patch("time.sleep", return_value=None):
            report = self.discovery.investigate(self.job_target)

        # Como la única pieza que había murió, no quedan piezas vivas -> CLEAN_RESTART
        self.assertEqual(report.decision, ExecutionDecision.CLEAN_RESTART)
        r0_refreshed = RemotePayload.get_by_id(r0.id)
        self.assertTrue(r0_refreshed.is_orphaned)


if __name__ == "__main__":
    unittest.main()
