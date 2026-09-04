import json
import lzma
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from tartape.schemas import EntryState

from totelegram.database import DatabaseSession
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TapeMember,
    TapeMemberGPS,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.schemas import MANIFEST_VERSION
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import (
    JobStatus,
    SourceType,
    Strategy,
    StrategyConfig,
    TapeCatalog,
)


class TestSnapshotService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

        # Iniciar sesión de base de datos SQLite en memoria con esquema v2
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
        # str(msg) devuelve un JSON válido para RemotePayload.json_metadata
        msg.__str__.return_value = json.dumps(  # type: ignore
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
            strategy=Strategy.SINGLE,
            status=JobStatus.UPLOADED,
            config=StrategyConfig(
                tg_max_size=2000 * 1024 * 1024,
                user_is_premium=False,
                app_version="0.9.15",
            ),
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
            manifest = SnapshotService.generate_snapshot(job)

        # 1. Validar retorno en memoria
        self.assertEqual(manifest.manifest_version, MANIFEST_VERSION)
        self.assertEqual(manifest.source.filename, "documento.pdf")
        self.assertEqual(manifest.source.md5sum, "hash_pdf_12345")
        self.assertEqual(len(manifest.parts), 1)
        self.assertEqual(manifest.parts[0].message_id, 101)
        self.assertEqual(manifest.parts[0].link, "https://t.me/c/123456789/101")

        # 2. Validar archivo físico comprimido en disco
        snapshot_file = file_path.with_name(f"{file_path.name}.json.xz")
        self.assertTrue(
            snapshot_file.exists(), "El archivo .json.xz no fue creado en disco"
        )

        with lzma.open(snapshot_file, "rt", encoding="utf-8") as f:
            disk_data = json.load(f)
            self.assertEqual(disk_data["manifest_version"], MANIFEST_VERSION)
            self.assertEqual(disk_data["owner_id"], self.user.id)
            self.assertEqual(disk_data["parts"][0]["part_md5sum"], "hash_pdf_12345")

    def test_generate_snapshot_for_folder_with_tape_inventory(self):
        """Genera y valida un snapshot completo para una carpeta empaquetada (TAR)."""
        folder_path = self.base_path / "mis_documentos"
        folder_path.mkdir()

        catalog = TapeCatalog(
            fingerprint="fingerprint_folder_abc",
            total_size=1500,
            total_files=2,
            created_at=1700000000.0,
            tartape_version="2.2.0",
            exclude_patterns="[]",
        )

        source = Source.create(
            path_str=str(folder_path),
            md5sum=catalog.fingerprint,
            size=catalog.total_size,
            mtime=catalog.created_at,
            mimetype="application/x-tar",
            tape_catalog=catalog,
            type=SourceType.FOLDER,
        )

        job = Job.create(
            source=source,
            chat=self.chat,
            strategy=Strategy.CHUNKED,
            status=JobStatus.UPLOADED,
            config=StrategyConfig(
                tg_max_size=1000,
                user_is_premium=False,
                app_version="0.9.15",
            ),
        )

        # Crear 2 payloads (volúmenes de la cinta)
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
            end_offset=1500,
            size=500,
            filename="mis_documentos.tar.02-02",
            filename_short="fp_abc.tar.02-02",
            md5sum="vol2_md5",
        )

        # Registrar miembros internos y su GPS
        member = TapeMember.create(
            source=source,
            relative_path="sub/archivo.txt",
            size=1500,
            md5sum="file_md5_full",
        )
        TapeMemberGPS.create(
            member=member,
            payload=p1,
            state=EntryState.HEAD,
            offset_in_volume=512,
            bytes_in_volume=488,
        )
        TapeMemberGPS.create(
            member=member,
            payload=p2,
            state=EntryState.TAIL,
            offset_in_volume=0,
            bytes_in_volume=1012,
        )

        msg1 = self._create_mock_message(201, self.chat.id, "https://t.me/c/1/201")
        msg2 = self._create_mock_message(202, self.chat.id, "https://t.me/c/1/202")

        with patch(
            "totelegram.models.parse_message_json_data", side_effect=[msg1, msg2]
        ):
            RemotePayload.register_upload(payload=p1, tg_message=msg1, owner=self.user)
            RemotePayload.register_upload(payload=p2, tg_message=msg2, owner=self.user)
            manifest = SnapshotService.generate_snapshot(job)

        self.assertIsNotNone(manifest.source.inventory)
        assert manifest.source.inventory is not None
        self.assertEqual(len(manifest.source.inventory), 1)
        self.assertEqual(manifest.source.inventory[0].relative_path, "sub/archivo.txt")
        self.assertEqual(len(manifest.source.inventory[0].fragments), 2)
        self.assertEqual(manifest.source.inventory[0].fragments[0].vol_idx, 0)
        self.assertEqual(manifest.source.inventory[0].fragments[1].vol_idx, 1)

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
            strategy=Strategy.SINGLE,
            status=JobStatus.PENDING,
            config=StrategyConfig(
                tg_max_size=100, user_is_premium=False, app_version="0.9.15"
            ),
        )

        with self.assertRaises(ValueError):
            SnapshotService.generate_snapshot(job)


if __name__ == "__main__":
    unittest.main()
