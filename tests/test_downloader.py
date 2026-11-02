import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import tartape

from totelegram.database import DatabaseSession
from totelegram.engine.downloader import DownloadEngine
from totelegram.models import (
    Job,
    RemotePayload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.snapshot import SnapshotService


class TestDownloadEngine(unittest.TestCase):
    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.mock_client = MagicMock()
        self.chat = TelegramChat.create(
            id=-100123456, title="Canal Downloader", type="channel"
        )
        self.user = TelegramUser.create(
            id=12345, first_name="DownloaderUser", is_premium=False
        )
        self.settings = MagicMock(exclude_files=[])

    def tearDown(self):
        self.db_session.close()

    def test_downloader_restores_empty_zero_byte_file_without_error(self):
        """Valida que un archivo de 0 bytes dentro de una cinta TAR se restaure
        directamente sin fallar por falta de fragmentos (cortocircuito)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            source_dir = base_dir / "proyecto"
            source_dir.mkdir()

            (source_dir / "app.py").write_text("print('hello')")
            (source_dir / "__init__.py").write_bytes(b"")

            tape = tartape.record(source_dir, checksum="md5", overwrite=True)
            source = Source.create_from_tape(tape, exclusion_patterns=[])
            job = Job.formalize_intent(source, self.chat, 10000)
            job.prepare_chunks(source_dir, self.settings)

            payload = job.payloads.first()
            mock_msg = MagicMock(id=881, chat=MagicMock(id=self.chat.id), empty=False)
            mock_msg.__str__.return_value = json.dumps(
                {
                    "id": 881,
                    "chat": {"id": self.chat.id, "type": "channel"},
                    "link": "https://t.me/c/123456/881",
                }
            )
            RemotePayload.register_upload(payload, mock_msg, self.user)

            manifest, snapshot_path = SnapshotService.generate_snapshot(job)

            restore_dir = base_dir / "restored"
            engine = DownloadEngine(client=self.mock_client)

            report = engine.restore_single_file(
                manifest=manifest,
                relative_path="proyecto/__init__.py",
                output_dir=restore_dir,
                snapshot_path=snapshot_path,
                force=True,
            )

            self.assertTrue(report.is_success)
            self.assertEqual(report.total_bytes_downloaded, 0)

            restored_file = restore_dir / "proyecto/__init__.py"
            self.assertTrue(restored_file.exists())
            self.assertEqual(restored_file.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
