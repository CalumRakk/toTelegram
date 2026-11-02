import json
import lzma
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import MagicMock, patch

from pyrogram.errors import FloodWait

from totelegram.database import DatabaseSession
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TelegramChat,
    TelegramUser,
)
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
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import SourceType


class TestSnapshotService(unittest.TestCase):
    """Pruebas de generación y validación de snapshots (.json.xz y .ttape)."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.chat = TelegramChat.create(
            id=-100123456789, title="Channel Test", type="channel"
        )
        self.user = TelegramUser.create(
            id=987654321, first_name="TestUser", username="tester", is_premium=False
        )

    def tearDown(self):
        self.db_session.close()
        self.temp_dir.cleanup()

    def _create_mock_message(self, message_id: int, chat_id: int, link: str):
        msg = MagicMock()
        msg.id = message_id
        msg.chat = MagicMock(id=chat_id, type="channel")
        msg.link = link
        msg.__str__.return_value = json.dumps(
            {
                "id": message_id,
                "chat": {"id": chat_id, "type": "channel"},
                "link": link,
            }
        )
        return msg

    def test_generate_snapshot_for_single_file(self):
        """Genera y valida un snapshot (.json.xz) para un archivo individual."""
        file_path = self.base_path / "documento.pdf"
        file_path.write_bytes(b"Contenido binario simulado del documento PDF")

        source = Source.create(
            path_str=str(file_path),
            md5sum="hash_pdf_12345",
            size=len(file_path.read_bytes()),
            mtime=1700000000.0,
            mimetype="application/pdf",
            type=SourceType.FILE,
        )

        job = Job.create(
            source=source,
            chat=self.chat,
            chunk_size=2000 * 1024 * 1024,
        )

        payload = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=source.size,
            size=source.size,
            filename="documento.pdf",
            filename_short="documento.pdf",
            md5sum="hash_pdf_12345",
        )

        mock_msg = self._create_mock_message(
            101, self.chat.id, "https://t.me/c/123456789/101"
        )
        with patch("totelegram.models.parse_message_json_data", return_value=mock_msg):
            RemotePayload.register_upload(
                payload=payload, tg_message=mock_msg, owner=self.user
            )
            manifest, _ = SnapshotService.generate_snapshot(job)

        self.assertEqual(manifest.manifest_version, MANIFEST_VERSION)
        self.assertEqual(manifest.source.filename, "documento.pdf")
        self.assertEqual(manifest.source.md5sum, "hash_pdf_12345")
        self.assertEqual(len(manifest.parts), 1)
        self.assertEqual(manifest.parts[0].message_id, 101)
        self.assertEqual(manifest.parts[0].link, "https://t.me/c/123456789/101")

        snapshot_file = file_path.with_name(f"{file_path.name}.json.xz")
        self.assertTrue(snapshot_file.exists())

        with lzma.open(snapshot_file, "rt", encoding="utf-8") as f:
            disk_data = json.load(f)
            self.assertEqual(disk_data["manifest_version"], MANIFEST_VERSION)
            self.assertEqual(disk_data["owner_id"], self.user.id)
            self.assertEqual(disk_data["parts"][0]["part_md5sum"], "hash_pdf_12345")

    def test_generate_snapshot_for_folder_with_tape_inventory(self):
        """Genera y valida un snapshot contenedor (.ttape) para una carpeta archivada."""
        import tartape

        from totelegram.packaging.schemas import TTAPE_EXTENSION
        from totelegram.packaging.ttape import TapeContainer

        folder_path = self.base_path / "mis_documentos"
        folder_path.mkdir()
        sub_dir = folder_path / "sub"
        sub_dir.mkdir()
        (sub_dir / "archivo.txt").write_bytes(b"A" * 800)

        tape = tartape.record(folder_path, checksum="md5", overwrite=True)
        self.assertTrue(tape.catalog_path.exists())

        source = Source.create_from_tape(tape, exclusion_patterns=[])
        job = Job.create(
            source=source,
            chat=self.chat,
            chunk_size=1000,
        )

        p1 = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=1000,
            size=1000,
            filename="mis_documentos.tar.01-02",
            filename_short="fp_abc.tar.01-02",
            md5sum="vol1_md5",
        )
        p2 = Payload.create(
            job=job,
            sequence_index=1,
            start_offset=1000,
            end_offset=source.size,
            size=max(0, source.size - 1000),
            filename="mis_documentos.tar.02-02",
            filename_short="fp_abc.tar.02-02",
            md5sum="vol2_md5",
        )

        msg1 = self._create_mock_message(201, self.chat.id, "https://t.me/c/1/201")
        msg2 = self._create_mock_message(202, self.chat.id, "https://t.me/c/1/202")

        with patch(
            "totelegram.models.parse_message_json_data", side_effect=[msg1, msg2]
        ):
            RemotePayload.register_upload(payload=p1, tg_message=msg1, owner=self.user)
            RemotePayload.register_upload(payload=p2, tg_message=msg2, owner=self.user)
            manifest, _ = SnapshotService.generate_snapshot(job)

        self.assertEqual(len(manifest.parts), 2)

        expected_ttape = folder_path.with_name(f"{folder_path.name}{TTAPE_EXTENSION}")
        self.assertTrue(expected_ttape.exists())
        self.assertFalse(tape.catalog_path.exists())

        with TapeContainer.open_catalog(expected_ttape) as cat:
            self.assertEqual(cat.fingerprint, tape.fingerprint)
            track = cat.locate("mis_documentos/sub/archivo.txt")
            self.assertEqual(track.file_size, 800)

    def test_snapshot_fails_without_remotes(self):
        """Debe lanzar ValueError si se intenta generar un snapshot de un Job sin piezas subidas."""
        file_path = self.base_path / "vacio.txt"
        file_path.write_text("demo")

        source = Source.create(
            path_str=str(file_path),
            md5sum="h_empty",
            size=4,
            mtime=1.0,
            mimetype="text/plain",
            type=SourceType.FILE,
        )
        job = Job.create(
            source=source,
            chat=self.chat,
            chunk_size=100,
        )

        with self.assertRaises(ValueError):
            SnapshotService.generate_snapshot(job)


class TestSnapshotPublisher(unittest.TestCase):
    """Pruebas de publicación de snapshots a Telegram con reintentos y control de FloodWait."""

    def setUp(self):
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


if __name__ == "__main__":
    unittest.main()
