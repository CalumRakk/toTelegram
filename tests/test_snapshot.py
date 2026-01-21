import json
import lzma
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from peewee import SqliteDatabase

from totelegram.core.enums import Strategy
from totelegram.services.snapshot import SnapshotService
from totelegram.store.models import (
    Job,
    Payload,
    RemotePayload,
    SourceFile,
    TelegramChat,
    TelegramUser,
    db_proxy,
)


class TestSnapshotIntegrity(unittest.TestCase):
    def setUp(self):
        # DB en memoria para tests
        self.test_db = SqliteDatabase(":memory:")
        db_proxy.initialize(self.test_db)
        self.test_db.create_tables(
            [SourceFile, Job, TelegramChat, Payload, RemotePayload, TelegramUser]
        )

        self.temp_dir = TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_rehydration_seed_completeness(self):
        """Verifica que el manifiesto contenga todo para reconstruir la DB."""
        # 1. Crear entorno de datos
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

        # Simulamos un Job ya configurado (ADR-002)
        from totelegram.core.schemas import StrategyConfig

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

        # 2. Ejecutar generador
        manifest = SnapshotService.generate_snapshot(job)

        # 3. Validar archivo físico
        snapshot_file = file_path.with_name(f"{file_path.name}.json.xz")
        self.assertTrue(snapshot_file.exists())

        # 4. Validar contenido de rehidratación
        with lzma.open(snapshot_file, "rt") as f:
            data = json.load(f)

            # ¿Están los campos de reconstrucción de Job?
            self.assertEqual(data["chunk_size"], 500)
            self.assertEqual(data["strategy"], "pieces-file")

            # ¿Están los campos de reconstrucción de Payload?
            part = data["parts"][0]
            self.assertEqual(part["part_md5sum"], "part_md5")
            self.assertEqual(part["message_id"], 99)

            # ¿Está la identidad del dueño?
            self.assertEqual(data["owner_id"], 123)


if __name__ == "__main__":
    unittest.main()
