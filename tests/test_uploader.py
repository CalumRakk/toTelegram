import json
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, patch

from totelegram.concurrency import ConcurrencyCoordinator
from totelegram.database import DatabaseSession
from totelegram.engine.events import ProgressMultiplexer, UploadObserver
from totelegram.engine.factory import PayloadStreamFactory, UploadStream
from totelegram.engine.uploader import CooperativeUploader
from totelegram.models import (
    Job,
    Payload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.schemas import SourceType
from totelegram.stream import FileVolume
from totelegram.types import UploadContext


class TestPayloadStreamFactoryAndEvents(unittest.TestCase):
    """Pruebas para la fábrica de flujos de subida y eventos de multiplexión."""

    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.chat = TelegramChat.create(
            id=-100123456, title="Canal Streams", type="channel"
        )
        self.settings = MagicMock(exclude_files=[])

    def tearDown(self):
        self.db_session.close()

    def test_payload_stream_factory_naming_resolution(self):
        """Si el nombre <= 55 caracteres va limpio; si es > 55 usa nombre corto y pasa el largo a caption."""
        source = Source.create(
            path_str="/tmp/test.txt",
            md5sum="hash1",
            size=100,
            mtime=1.0,
            mimetype="text/plain",
        )
        job = Job.formalize_intent(source, self.chat, 1000)

        p_short = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=100,
            size=100,
            filename="foto_vacaciones.jpg",
            filename_short="h123.jpg",
        )
        name, caption = PayloadStreamFactory.resolve_naming(
            p_short, max_filename_len=55
        )
        self.assertEqual(name, "foto_vacaciones.jpg")
        self.assertIn("#totelegram_chunk", caption)
        self.assertIn("#src_hash1", caption)

        long_name = "a" * 60 + ".jpg"
        p_long = Payload.create(
            job=job,
            sequence_index=1,
            start_offset=0,
            end_offset=100,
            size=100,
            filename=long_name,
            filename_short="short_hash.jpg",
        )
        name_res, caption_res = PayloadStreamFactory.resolve_naming(
            p_long, max_filename_len=55
        )
        self.assertEqual(name_res, "short_hash.jpg")
        self.assertIn("#totelegram_chunk", caption_res)
        self.assertIn("#src_hash1", caption_res)
        self.assertIn("a" * 30, caption_res)

    def test_payload_stream_factory_creates_file_volume(self):
        """Para SourceType.FILE, create_stream debe retornar un UploadStream conteniendo FileVolume."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"data" * 25)
            tmp_path = Path(tmp.name)

        try:
            source = Source.get_or_create_from_filepath(tmp_path)
            job = Job.formalize_intent(source, self.chat, 1000)
            job.prepare_chunks(tmp_path, self.settings)
            payload = job.payloads.first()

            upload_stream = PayloadStreamFactory.create_stream(payload, tmp_path)
            self.assertIsInstance(upload_stream, UploadStream)
            self.assertIsInstance(upload_stream.stream, FileVolume)
            self.assertEqual(upload_stream.size, 100)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_progress_multiplexer_dispatch(self):
        """Verifica que el multiplexor notifique al heartbeat (DB) y al observer (UI) en cada chunk."""
        mock_heartbeat = MagicMock()
        mock_observer = MagicMock()

        multiplexer = ProgressMultiplexer(
            heartbeat=mock_heartbeat, observer=mock_observer
        )
        multiplexer(current=500, total=1000)

        mock_heartbeat.pulse.assert_called_once_with(500, 1000)
        mock_observer.on_payload_progress.assert_called_once_with(500, 1000)


class TestCooperativeUploader(unittest.TestCase):
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
            coordinator=self.coordinator,
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

    def test_cooperative_uploader_full_flow(self):
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

            uploader = CooperativeUploader()

            with patch("time.sleep", return_value=None):
                result = uploader.upload(
                    job, session=self.ctx, observer=self.mock_observer
                )

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
