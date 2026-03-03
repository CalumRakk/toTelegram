import json
import lzma
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from totelegram.common.enums import Strategy
from totelegram.logic.snapshot import SnapshotService
from totelegram.manager.database import DatabaseSession
from totelegram.manager.models import (
    Job,
    Payload,
    RemotePayload,
    SourceFile,
    TelegramChat,
    TelegramUser,
)


class TestSnapshotIntegrity(unittest.TestCase):
    def setUp(self):

        self.test_db = DatabaseSession(":memory:")
        self.test_db.start()

        self.temp_dir = TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()
        self.test_db.close()

    def test_rehydration_seed_completeness(self):
        """Verifica que el manifiesto contenga todo para reconstruir la DB."""
        chat = TelegramChat.create(id=-1001, title="Test Chat", type="channel")
        user = TelegramUser.create(id=123, first_name="Tester")

        file_path = self.base_path / "video.mp4"
        file_path.write_bytes(b"contenido ficticio")

        source = SourceFile.create(
            path_str=str(file_path),
            md5sum="original_md5",
            size=1000,
            mtime=123456.7,
            mimetype="video/mp4",
        )

        from totelegram.common.schemas import StrategyConfig

        config = StrategyConfig(
            tg_max_size=500, user_is_premium=False, app_version="0.1"
        )

        job = Job.create(
            source=source,
            chat=chat,
            strategy=Strategy.CHUNKED,
            status="UPLOADED",
            config=config,
        )

        # Creamos una parte con su registro remoto
        p = Payload.create(
            job=job, sequence_index=0, md5sum="part_md5", size=500, temp_path="v.part1"
        )

        # El JSON_METADATA es vital para el link
        fake_msg = {
            "message_id": 99,
            "chat": {"id": -1001, "type": "channel"},
            "link": "https://t.me/c/1/99",
        }
        RemotePayload.create(
            payload=p,
            message_id=99,
            chat=chat,
            owner=user,
            json_metadata=json.dumps(fake_msg),
        )

        SnapshotService.generate_snapshot(job)

        # Verificamos que el manifiesta se haya formado bien.
        snapshot_file = file_path.with_name(f"{file_path.name}.json.xz")
        self.assertTrue(snapshot_file.exists())
        with lzma.open(snapshot_file, "rt") as f:
            data = json.load(f)

            self.assertEqual(data["chunk_size"], 500)
            self.assertEqual(data["strategy"], "pieces-file")

            part = data["parts"][0]
            self.assertEqual(part["part_md5sum"], "part_md5")
            self.assertEqual(part["message_id"], 99)

            self.assertEqual(data["owner_id"], 123)


if __name__ == "__main__":
    unittest.main()
