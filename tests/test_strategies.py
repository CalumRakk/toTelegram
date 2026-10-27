import json
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, patch

from totelegram.concurrency import ConcurrencyCoordinator
from totelegram.database import DatabaseSession
from totelegram.engine.events import UploadObserver
from totelegram.engine.strategies.upload import CooperativeUploadStrategy
from totelegram.models import (
    Job,
    Payload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.schemas import AvailabilityState, ExecutionDecision, SourceType
from totelegram.types import InvestigationReport, UploadContext


class TestCooperativeUploadStrategy(unittest.TestCase):
    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.mock_client = MagicMock()
        self.mock_observer = MagicMock(spec=UploadObserver)
        self.coordinator = ConcurrencyCoordinator(self.db, node_id="test_worker_node")

        self.chat = TelegramChat.create(
            id=-100123456, title="Canal Principal", type="channel"
        )
        self.user = TelegramUser.create(
            id=999, first_name="StrategyUser", is_premium=False
        )

        self.settings = MagicMock()
        self.settings.chat_id = self.chat.id
        self.settings.telegram_account_id = self.user.id
        self.settings.upload_limit_rate_kbps = 0
        self.settings.max_filename_length = 55
        self.settings.upload_pause_range = [0, 0]
        self.settings.exclude_files = []

        self.ctx = UploadContext(
            tg_chat=MagicMock(id=self.chat.id, title=self.chat.title),
            owner=self.user,
            client=self.mock_client,
            db=self.db,
            discovery=MagicMock(),
            settings=self.settings,
            state=MagicMock(),
            coordinator=self.coordinator,
            observer=self.mock_observer,
        )

    def tearDown(self):
        self.db_session.close()

    def _create_mock_tg_message(self, message_id: int, chat_id: int):
        msg = MagicMock()
        msg.id = message_id
        msg.chat = MagicMock(id=chat_id)
        msg.link = f"https://t.me/c/{abs(chat_id)}/{message_id}"
        msg.document = MagicMock(file_id=f"file_id_{message_id}", file_size=100)
        msg.__str__.return_value = json.dumps(
            {
                "id": message_id,
                "chat": {"id": chat_id},
                "link": msg.link,
                "document": {"file_id": f"file_id_{message_id}", "file_size": 100},
            }
        )
        return msg

    def test_cooperative_upload_strategy_full_flow(self):
        """Valida la subida física cooperativa pieza a pieza con notificación a eventos."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"Z" * 250)
            tmp_path = Path(tmp.name)

        try:
            source = Source.create(
                path_str=str(tmp_path),
                md5sum="hash_coop_file_250",
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

            # Simular 3 mensajes subidos devueltos por Pyrogram
            msg_up0 = self._create_mock_tg_message(701, self.chat.id)
            msg_up1 = self._create_mock_tg_message(702, self.chat.id)
            msg_up2 = self._create_mock_tg_message(703, self.chat.id)
            self.mock_client.send_document.side_effect = [msg_up0, msg_up1, msg_up2]

            report = InvestigationReport(decision=ExecutionDecision.CLEAN_RESTART)

            strategy = CooperativeUploadStrategy()

            with patch("time.sleep", return_value=None):
                result = strategy.execute(job, self.ctx, report)

            self.assertTrue(result.is_completed)
            self.assertTrue(result.did_upload_bytes)
            self.assertEqual(result.pieces_processed, 3)
            self.assertEqual(self.mock_client.send_document.call_count, 3)

            # Verificar que todos los payloads quedaron con MD5 y RemotePayload registrado
            self.assertEqual(Payload.total_pending_for_job(job), 0)

            # Verificar hooks del observador
            self.assertEqual(self.mock_observer.on_payload_start.call_count, 3)
            self.assertEqual(self.mock_observer.on_payload_complete.call_count, 3)

        finally:
            if tmp_path.exists():
                tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
