import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, patch

from pyrogram.errors import FloodWait

from totelegram.database import DatabaseSession
from totelegram.models import Job, Payload, Source, TelegramChat, TelegramUser
from totelegram.packaging.publisher import (
    SnapshotPublicationError,
    SnapshotPublisher,
)
from totelegram.packaging.schemas import (
    MANIFEST_VERSION,
    RemotePart,
    SourceMetadata,
    UploadManifest,
)
from totelegram.schemas import JobStatus, SourceType


class TestSnapshotPublisher(unittest.TestCase):
    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.mock_client = MagicMock()
        self.chat_id = -100123456789

        self.manifest = UploadManifest(
            manifest_version=MANIFEST_VERSION,
            app_version="0.9.25",
            created_at=datetime.now(timezone.utc),
            chunk_size=1000,
            chat_id=self.chat_id,
            owner_id=12345,
            owner_name="Tester",
            source=SourceMetadata(
                filename="documento.pdf",
                size=500,
                md5sum="4f5ab2c1e8d4a6b7c8d9e0f1a2b3c4d5",
                mime_type="application/pdf",
                mtime=1700000000.0,
                type=SourceType.FILE,
            ),
            parts=[
                RemotePart(
                    sequence=0,
                    message_id=101,
                    chat_id=self.chat_id,
                    link="https://t.me/c/123456789/101",
                    part_filename="documento.pdf",
                    part_size=500,
                    part_md5sum="4f5ab2c1e8d4a6b7c8d9e0f1a2b3c4d5",
                    start_offset=0,
                    end_offset=500,
                )
            ],
        )

    def tearDown(self):
        self.db_session.close()

    def test_publish_success(self):
        """Valida la subida exitosa de un archivo snapshot a Telegram con su caption."""
        with NamedTemporaryFile(suffix=".json.xz", delete=False) as tmp:
            tmp.write(b"fake_snapshot_content")
            tmp_path = Path(tmp.name)

        try:
            sent_msg = MagicMock()
            sent_msg.id = 8888
            self.mock_client.send_document.return_value = sent_msg

            result = SnapshotPublisher.publish(
                client=self.mock_client,
                chat_id=self.chat_id,
                snapshot_path=tmp_path,
                manifest=self.manifest,
            )

            self.assertEqual(result.id, 8888)
            self.mock_client.send_document.assert_called_once()
            _, kwargs = self.mock_client.send_document.call_args
            self.assertEqual(kwargs["chat_id"], self.chat_id)
            self.assertEqual(kwargs["file_name"], tmp_path.name)
            self.assertIn("#totelegram_manifest", kwargs["caption"])
            self.assertIn("#type_file", kwargs["caption"])
            self.assertIn("#src_4f5ab2c1e8d4a6b7c8d9e0f1a2b3c4d5", kwargs["caption"])
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_publish_handles_flood_wait_and_retries(self):
        """Si Telegram responde con FloodWait, debe pausar y reintentar exitosamente."""
        with NamedTemporaryFile(suffix=".json.xz", delete=False) as tmp:
            tmp.write(b"data")
            tmp_path = Path(tmp.name)

        try:
            sent_msg = MagicMock()
            sent_msg.id = 9999

            # Primer intento: FloodWait de 2 segundos. Segundo intento: éxito.
            self.mock_client.send_document.side_effect = [
                FloodWait(value=2),
                sent_msg,
            ]

            with patch("time.sleep") as mock_sleep:
                result = SnapshotPublisher.publish(
                    client=self.mock_client,
                    chat_id=self.chat_id,
                    snapshot_path=tmp_path,
                    manifest=self.manifest,
                )

            self.assertEqual(result.id, 9999)
            self.assertEqual(self.mock_client.send_document.call_count, 2)
            mock_sleep.assert_called_once()
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_publish_exhausts_retries_raises_error(self):
        """Si se agotan los reintentos, debe elevar SnapshotPublicationError."""
        with NamedTemporaryFile(suffix=".json.xz", delete=False) as tmp:
            tmp.write(b"data")
            tmp_path = Path(tmp.name)

        try:
            self.mock_client.send_document.side_effect = FloodWait(value=1)

            with patch("time.sleep"):
                with self.assertRaises(SnapshotPublicationError):
                    SnapshotPublisher.publish(
                        client=self.mock_client,
                        chat_id=self.chat_id,
                        snapshot_path=tmp_path,
                        manifest=self.manifest,
                    )
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
