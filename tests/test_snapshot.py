import json
import lzma
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from totelegram.database import DatabaseSession
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TapeMember,
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

        # Validar retorno en memoria
        self.assertEqual(manifest.manifest_version, MANIFEST_VERSION)
        self.assertEqual(manifest.source.filename, "documento.pdf")
        self.assertEqual(manifest.source.md5sum, "hash_pdf_12345")
        self.assertEqual(len(manifest.parts), 1)
        self.assertEqual(manifest.parts[0].message_id, 101)
        self.assertEqual(manifest.parts[0].link, "https://t.me/c/123456789/101")

        # Validar archivo físico comprimido en disco
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
        """Genera y valida un snapshot contenedor (.ttape) para una carpeta archivada."""
        import tartape

        from totelegram.packaging.schemas import TTAPE_EXTENSION
        from totelegram.packaging.ttape import TapeContainer

        folder_path = self.base_path / "mis_documentos"
        folder_path.mkdir()
        sub_dir = folder_path / "sub"
        sub_dir.mkdir()
        (sub_dir / "archivo.txt").write_bytes(b"A" * 800)

        # 1. tartape genera el catálogo SQLite T0 (.tartape)
        tape = tartape.record(folder_path, checksum="md5", overwrite=True)
        self.assertTrue(tape.catalog_path.exists())

        source = Source.create_from_tape(tape, exclusion_patterns=[])

        job = Job.create(
            source=source,
            chat=self.chat,
            strategy=Strategy.CHUNKED,
            status=JobStatus.UPLOADED,
            config=StrategyConfig(
                tg_max_size=1000,
                user_is_premium=False,
                app_version="0.9.25",
            ),
        )

        # Crear 2 payloads simulando volúmenes
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
            manifest = SnapshotService.generate_snapshot(job)

        # 2. Validaciones bajo el estándar v6.0 (.ttape)
        # En v6.0, el JSON ya NO tiene el array inventory (RAM O(1))
        self.assertIsNone(manifest.source.inventory)
        self.assertEqual(len(manifest.parts), 2)

        # Debe generar físicamente el contenedor .ttape
        expected_ttape = folder_path.with_name(f"{folder_path.name}{TTAPE_EXTENSION}")
        self.assertTrue(
            expected_ttape.exists(), "El contenedor .ttape no fue creado en disco"
        )

        # Cero residuos: el catálogo suelto .tartape debe haber sido absorbido y eliminado
        self.assertFalse(
            tape.catalog_path.exists(), "El catálogo suelto .tartape no fue limpiado"
        )

        # El catálogo SQLite dentro del .ttape debe ser legible y contener los archivos
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
            strategy=Strategy.SINGLE,
            status=JobStatus.PENDING,
            config=StrategyConfig(
                tg_max_size=100, user_is_premium=False, app_version="0.9.15"
            ),
        )

        with self.assertRaises(ValueError):
            SnapshotService.generate_snapshot(job)

    def test_tape_member_get_slices_atomic_math(self):
        """Verifica que TapeMember.get_slices calcule fragmentos exactos en O(1)."""
        source = Source.create(
            path_str="/tmp/test_tape",
            md5sum="hash_tape_slices_test",
            size=5000,
            mtime=1.0,
            mimetype="application/x-tar",
            type=SourceType.FOLDER,
        )

        # Archivo que empieza en offset 1500 (TAR header) y mide 1200 bytes
        # Con volúmenes de 1000 bytes:
        # Contenido raw: [1500 + 512 = 2012] hasta [2012 + 1200 = 3212]
        # Cruza Volumen 2 (2000-3000) y Volumen 3 (3000-4000)
        member = TapeMember.create(
            source=source,
            relative_path="docs/manual.pdf",
            size=1200,
            md5sum="md5_manual",
            start_offset=1500,
        )

        slices = member.get_slices(volume_size=1000)
        self.assertEqual(len(slices), 2)

        # Parte 1 (en volumen 2): del byte 2012 al 3000 -> offset 12, longitud 988
        self.assertEqual(slices[0].volume_index, 2)
        self.assertEqual(slices[0].volume_offset, 12)
        self.assertEqual(slices[0].volume_length, 988)
        self.assertEqual(slices[0].source_offset, 0)

        # Parte 2 (en volumen 3): del byte 3000 al 3212 -> offset 0, longitud 212
        self.assertEqual(slices[1].volume_index, 3)
        self.assertEqual(slices[1].volume_offset, 0)
        self.assertEqual(slices[1].volume_length, 212)
        self.assertEqual(slices[1].source_offset, 988)

    def test_import_snapshot_heals_missing_start_offset(self):
        """Al importar un snapshot antiguo con start_offset en 0, debe sanarlo en BD."""
        from datetime import datetime, timezone

        from totelegram.packaging.importer import (
            DbMutations,
            InspectionStatus,
            PlanAction,
            PlanItem,
            SnapshotImporter,
            SnapshotInspection,
        )
        from totelegram.packaging.schemas import (
            FileFragment,
            RemotePart,
            SourceMetadata,
            TapeMemberSnapshot,
            UploadManifest,
        )

        manifest = UploadManifest(
            manifest_version="5.1",
            strategy=Strategy.CHUNKED,
            chunk_size=1000,
            created_at=datetime.now(timezone.utc),
            chat_id=self.chat.id,
            owner_id=self.user.id,
            owner_name="Test",
            parts=[
                RemotePart(
                    sequence=0,
                    message_id=1,
                    chat_id=self.chat.id,
                    link="",
                    part_filename="v.tar.01",
                    part_size=1000,
                    part_md5sum="m1",
                    start_offset=0,
                    end_offset=1000,
                ),
                RemotePart(
                    sequence=1,
                    message_id=2,
                    chat_id=self.chat.id,
                    link="",
                    part_filename="v.tar.02",
                    part_size=500,
                    part_md5sum="m2",
                    start_offset=1000,
                    end_offset=1500,
                ),
            ],
            source=SourceMetadata(
                filename="backup.tar",
                size=1500,
                md5sum="hash_snap_heal_test",
                mime_type="application/x-tar",
                mtime=1.0,
                type=SourceType.FOLDER,
                inventory=[
                    # Simula un snapshot viejo: start_offset viene en 0, pero fragments tiene datos
                    TapeMemberSnapshot(
                        relative_path="archivo.txt",
                        size=300,
                        md5sum="md5_arch",
                        start_offset=0,  # <--- Antiguo
                        fragments=[
                            FileFragment(
                                vol_idx=0,
                                volume_offset=600,
                                volume_length=300,
                                source_offset=0,
                            )
                        ],
                    )
                ],
            ),
        )

        item = PlanItem(
            inspection=SnapshotInspection(
                path=Path("/tmp/fake.json.xz"),
                status=InspectionStatus.VALID,
                manifest=manifest,
            ),
            manifest=manifest,
            action=PlanAction.CREATE_NEW,
            target_chat_id=self.chat.id,
            total_parts=2,
            mutations=DbMutations(
                create_source=True,
                create_job=True,
                payloads_count=2,
                remotes_count=2,
                members_count=1,
            ),
        )

        succeeded, errors = SnapshotImporter.apply_plan(
            plan=type("MockPlan", (), {"actionable_items": [item]})(),
            db=self.db,
            owner=self.user,
        )

        self.assertEqual(succeeded, 1)
        self.assertEqual(len(errors), 0)

        # Validar que start_offset fue calculado: (0 * 1000 + 600) - 512 = 88
        saved_member = TapeMember.get(TapeMember.relative_path == "archivo.txt")
        self.assertEqual(saved_member.start_offset, 88)


if __name__ == "__main__":
    unittest.main()
