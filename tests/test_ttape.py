import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import tartape

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
from totelegram.packaging.importer import (
    InspectionStatus,
    PlanItem,
    SnapshotImporter,
    SnapshotInspector,
)
from totelegram.packaging.schemas import (
    MANIFEST_VERSION,
    TTAPE_CATALOG_FILENAME,
    TTAPE_EXTENSION,
    TTAPE_MANIFEST_FILENAME,
    RemotePart,
    SourceMetadata,
    UploadManifest,
)
from totelegram.packaging.snapshot import SnapshotService
from totelegram.packaging.ttape import (
    InvalidTapeContainerError,
    TapeContainer,
)
from totelegram.schemas import JobStatus, SourceType


class TestTapeContainerAndBifurcation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

        # Base de datos SQLite aislada en memoria para toTelegram
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.chat = TelegramChat.create(
            id=-10099887766, title="Canal de Pruebas Tape", type="channel"
        )
        self.user = TelegramUser.create(
            id=11223344, first_name="TapeTester", is_premium=False
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

    # 1. PRUEBAS DEL CONTENEDOR HÍBRIDO TAPE CONTAINER (.ttape)

    def test_tape_container_build_and_read_manifest_o1(self):
        """Valida que TapeContainer empaquete el ZIP y permita leer manifest.json sin descomprimir SQLite."""
        # 1. Crear carpeta con archivos dummy y catalogar con tartape
        folder = self.base_path / "proyecto_codigo"
        folder.mkdir()
        (folder / "main.py").write_text("print('hello world')")
        (folder / "config.json").write_text('{"debug": true}')

        tape = tartape.record(folder, checksum="md5", overwrite=True)
        self.assertTrue(tape.catalog_path.exists())

        # 2. Construir un UploadManifest de prueba (v6.0)
        manifest = UploadManifest(
            manifest_version=MANIFEST_VERSION,
            created_at=datetime.now(timezone.utc),
            chunk_size=1024 * 1024,
            chat_id=self.chat.id,
            owner_id=self.user.id,
            owner_name=self.user.first_name,
            source=SourceMetadata(
                filename="proyecto_codigo",
                size=tape.total_size,
                md5sum=tape.fingerprint,
                mime_type="application/x-tar",
                mtime=1700000000.0,
                type=SourceType.FOLDER,
            ),
            parts=[
                RemotePart(
                    sequence=0,
                    message_id=501,
                    chat_id=self.chat.id,
                    link="https://t.me/c/1/501",
                    part_filename="proyecto_codigo.tar.01-01",
                    part_size=tape.total_size,
                    part_md5sum="hash_part_01",
                    start_offset=0,
                    end_offset=tape.total_size,
                )
            ],
        )

        ttape_dest = self.base_path / "proyecto_codigo.ttape"

        # 3. Empaquetar
        built_path = TapeContainer.build(
            output_path=ttape_dest,
            manifest=manifest,
            catalog_path=tape.catalog_path,
            overwrite=True,
        )

        self.assertTrue(built_path.exists())
        self.assertTrue(TapeContainer.is_valid(built_path))

        # 4. Lectura O(1) de manifest.json
        loaded_manifest = TapeContainer.read_manifest(built_path)
        self.assertEqual(loaded_manifest.manifest_version, MANIFEST_VERSION)
        self.assertEqual(loaded_manifest.source.filename, "proyecto_codigo")
        self.assertEqual(len(loaded_manifest.parts), 1)
        self.assertEqual(loaded_manifest.parts[0].message_id, 501)

        # 5. Inspección de tamaños y compresión
        info = TapeContainer.inspect(built_path)
        self.assertGreater(info.total_container_bytes, 0)
        self.assertGreater(info.catalog_compressed_bytes, 0)
        self.assertGreater(info.catalog_uncompressed_bytes, 0)

        # 6. Context manager open_catalog para consultas relacionales B-Tree
        with TapeContainer.open_catalog(built_path) as cat:
            self.assertEqual(cat.fingerprint, tape.fingerprint)
            self.assertEqual(cat.file_count, 3)  # root + main.py + config.json

            # Consulta quirúrgica a través del catálogo abierto
            gps_main = cat.locate("proyecto_codigo/main.py")
            self.assertIsNotNone(gps_main)
            self.assertEqual(gps_main.file_size, len("print('hello world')"))

    def test_tape_container_invalid_zip_raises_error(self):
        """Valida que un archivo dañado o sin los artefactos requeridos lance InvalidTapeContainerError."""
        corrupt_file = self.base_path / "corrupto.ttape"

        # Caso A: Archivo binario no ZIP
        corrupt_file.write_bytes(b"esto no es un zip")
        self.assertFalse(TapeContainer.is_valid(corrupt_file))
        with self.assertRaises(InvalidTapeContainerError):
            TapeContainer.read_manifest(corrupt_file)

        # Caso B: Archivo ZIP pero sin catalog.tartape
        corrupt_file.unlink()
        with zipfile.ZipFile(corrupt_file, "w") as zf:
            zf.writestr(TTAPE_MANIFEST_FILENAME, "{}")

        self.assertFalse(TapeContainer.is_valid(corrupt_file))
        with self.assertRaises(InvalidTapeContainerError):
            TapeContainer.extract_catalog(corrupt_file, self.base_path / "out.tartape")

    # 2. PRUEBAS DE BIFURCACIÓN EN SNAPSHOT SERVICE

    def test_snapshot_service_bifurcation_file_generates_json_xz(self):
        """Un SourceType.FILE debe generar exclusivamente .json.xz."""
        file_path = self.base_path / "reporte.pdf"
        file_path.write_bytes(b"%PDF-1.4 dummy content")

        source = Source.create(
            path_str=str(file_path),
            md5sum="hash_pdf_single",
            size=file_path.stat().st_size,
            mtime=1700000000.0,
            mimetype="application/pdf",
            type=SourceType.FILE,
        )

        job = Job.create(
            source=source,
            chat=self.chat,
            chunk_size=1000,
        )

        payload = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=source.size,
            size=source.size,
            filename="reporte.pdf",
            filename_short="reporte.pdf",
            md5sum="hash_pdf_single",
        )

        mock_msg = self._create_mock_message(101, self.chat.id, "https://t.me/c/1/101")
        RemotePayload.register_upload(payload, mock_msg, self.user)

        _, _ = SnapshotService.generate_snapshot(job)

        # Debe generar reporte.pdf.json.xz
        expected_snap = file_path.with_name(f"{file_path.name}.json.xz")
        self.assertTrue(expected_snap.exists())

        loaded = SnapshotService.load_snapshot(expected_snap)
        self.assertEqual(loaded.source.filename, "reporte.pdf")
        self.assertEqual(loaded.source.type, SourceType.FILE)
        self.assertIsNone(loaded.source.tape_catalog)

    def test_snapshot_service_bifurcation_folder_generates_ttape_and_cleans_residues(
        self,
    ):
        """Un SourceType.FOLDER debe generar un contenedor .ttape y borrar el .tartape suelto."""
        folder = self.base_path / "album_fotos"
        folder.mkdir()
        (folder / "foto1.jpg").write_bytes(b"\xff\xd8\xff" + b"A" * 100)
        (folder / "foto2.jpg").write_bytes(b"\xff\xd8\xff" + b"B" * 200)

        # 1. tartape genera el catálogo sidecar (.tartape) en disco
        tape = tartape.record(folder, checksum="md5", overwrite=True)
        catalog_path = tape.catalog_path
        self.assertTrue(catalog_path.exists())

        source = Source.create_from_tape(tape, exclusion_patterns=[])
        job = Job.formalize_intent(source, self.chat, 1024 * 1024)
        job.prepare_chunks(folder, MagicMock(exclude_files=[]))
        payload = job.payloads.first()

        mock_msg = self._create_mock_message(201, self.chat.id, "https://t.me/c/1/201")
        RemotePayload.register_upload(payload, mock_msg, self.user)

        # 2. Generar snapshot
        manifest, _ = SnapshotService.generate_snapshot(job)

        # 3. Comprobaciones de almacenamiento
        expected_ttape = folder.with_name(f"{folder.name}{TTAPE_EXTENSION}")
        self.assertTrue(expected_ttape.exists())

        # Cero residuos en disco: el .tartape suelto debe haber sido absorbido y eliminado
        self.assertFalse(catalog_path.exists())

        # El manifest en memoria y en .ttape debe tener tipo FOLDER y catalog
        self.assertEqual(manifest.source.type, SourceType.FOLDER)
        loaded = SnapshotService.load_snapshot(expected_ttape)
        self.assertEqual(loaded.source.filename, "album_fotos")
        self.assertEqual(loaded.source.type, SourceType.FOLDER)
        self.assertIsNotNone(loaded.source.tape_catalog)

    # 3. PRUEBAS DE INTEGRACIÓN: INSPECTOR & IMPORTADOR

    def test_snapshot_inspector_and_importer_direct_sql_from_ttape(self):
        """Valida que SnapshotInspector reconozca el .ttape y SnapshotImporter vuelque tracks a TapeMember."""
        folder = self.base_path / "documentos_legales"
        folder.mkdir()
        (folder / "contrato.pdf").write_bytes(b"CONTRATO PRIVADO")
        (folder / "anexo.pdf").write_bytes(b"ANEXO 1")

        tape = tartape.record(folder, checksum="md5", overwrite=True)
        source = Source.create_from_tape(tape, exclusion_patterns=[])
        job = Job.formalize_intent(source, self.chat, 5120)
        job.prepare_chunks(folder, MagicMock(exclude_files=[]))
        payload = job.payloads.first()

        mock_msg = self._create_mock_message(301, self.chat.id, "https://t.me/c/1/301")
        RemotePayload.register_upload(payload, mock_msg, self.user)

        SnapshotService.generate_snapshot(job)
        ttape_path = folder.with_name(f"{folder.name}{TTAPE_EXTENSION}")

        # A. SnapshotInspector
        candidates = SnapshotInspector.find_all_candidates([self.base_path])
        self.assertIn(ttape_path, candidates)

        inspection = SnapshotInspector.inspect_file(ttape_path)
        self.assertEqual(inspection.status, InspectionStatus.VALID)
        self.assertIsNotNone(inspection.manifest)

        # B. Simular base de datos vacía para probar rehidratación directa
        self.db.execute_sql("DELETE FROM tapemember;")
        self.db.execute_sql("DELETE FROM remotepayload;")
        self.db.execute_sql("DELETE FROM payload;")
        self.db.execute_sql("DELETE FROM job;")
        self.db.execute_sql("DELETE FROM source;")

        self.assertEqual(TapeMember.select().count(), 0)

        # C. Importación vía SnapshotImporter usando el .ttape
        item = PlanItem(
            inspection=inspection,
            manifest=inspection.manifest,
            action=MagicMock(),
            target_chat_id=self.chat.id,
            total_parts=len(inspection.manifest.parts),
            mutations=MagicMock(members_count=2, remotes_count=1),
        )

        mock_plan = MagicMock()
        mock_plan.actionable_items = [item]

        succeeded, errors = SnapshotImporter.apply_plan(
            plan=mock_plan, db=self.db, owner=self.user
        )

        self.assertEqual(succeeded, 1)
        self.assertEqual(len(errors), 0)

        # Verificar que TapeMember fue poblado mediante volcado masivo directo desde SQLite
        members = list(TapeMember.select().order_by(TapeMember.relative_path))
        self.assertEqual(
            len(members), 2
        )  # contrato.pdf y anexo.pdf (directorios excluidos)
        self.assertEqual(members[0].relative_path, "documentos_legales/anexo.pdf")
        self.assertGreater(members[0].start_offset, 0)
        self.assertEqual(members[1].relative_path, "documentos_legales/contrato.pdf")


if __name__ == "__main__":
    unittest.main()
