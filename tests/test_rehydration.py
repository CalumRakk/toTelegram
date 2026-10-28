import lzma
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from totelegram.database import DatabaseSession
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.importer import (
    InspectionStatus,
    RehydrationResult,
    SnapshotImporter,
    SnapshotImportPlanner,
    SnapshotInspector,
)
from totelegram.packaging.schemas import (
    MANIFEST_VERSION,
    RemotePart,
    SourceMetadata,
    UploadManifest,
)
from totelegram.schemas import SourceType


class TestRehydrationEngine(unittest.TestCase):
    def setUp(self):
        # Base de datos SQLite en memoria aislada para cada test
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.chat_id = -100123456789
        self.chat = TelegramChat.create(
            id=self.chat_id, title="Canal Rehidratación", type="channel"
        )
        self.user = TelegramUser.create(
            id=999888, first_name="RehydrateUser", is_premium=False
        )
        self.mock_client = MagicMock()
        self.mock_client.get_me.return_value = MagicMock(
            id=self.user.id, first_name=self.user.first_name, username="tester"
        )

    def tearDown(self):
        self.db_session.close()
        self.temp_dir.cleanup()

    def _create_synthetic_snapshot_file(
        self,
        filename: str,
        md5sum: str,
        manifest_version: str = MANIFEST_VERSION,
    ) -> Path:
        """Crea un archivo .json.xz real y válido en disco para las pruebas."""
        manifest = UploadManifest(
            manifest_version=manifest_version,
            created_at=datetime.now(timezone.utc),
            chunk_size=1024 * 1024,
            chat_id=self.chat_id,
            owner_id=self.user.id,
            owner_name=self.user.first_name,
            source=SourceMetadata(
                filename=filename,
                size=2048,
                md5sum=md5sum,
                mime_type="application/octet-stream",
                mtime=1700000000.0,
                type=SourceType.FILE,
            ),
            parts=[
                RemotePart(
                    sequence=0,
                    message_id=7701,
                    chat_id=self.chat_id,
                    link="https://t.me/c/1/7701",
                    part_filename=filename,
                    part_size=2048,
                    part_md5sum=md5sum,
                    start_offset=0,
                    end_offset=2048,
                )
            ],
        )
        out_path = self.base_path / f"{filename}.json.xz"
        with lzma.open(out_path, "wt", encoding="utf-8") as f:
            f.write(manifest.model_dump_json(indent=2))
        return out_path

    def _create_mock_tg_message(self, message_id: int, doc_name: str) -> MagicMock:
        msg = MagicMock()
        msg.id = message_id
        msg.empty = False
        msg.chat = MagicMock(id=self.chat_id)
        msg.document = MagicMock(file_name=doc_name, file_size=1000)
        return msg

    # PRUEBAS DE REHIDRATACIÓN INDIVIDUAL (JIT)

    def test_rehydrate_from_message_success(self):
        """Valida que un mensaje con snapshot válido reconstruya el Job y sus piezas en SQLite."""
        snap_file = self._create_synthetic_snapshot_file(
            "documento.pdf", "hash_doc_12345"
        )
        msg = self._create_mock_tg_message(101, "documento.pdf.json.xz")

        # Simular descarga exitosa copiando el archivo sintético
        def fake_download_media(message, file_name):
            target = Path(file_name)
            target.write_bytes(snap_file.read_bytes())
            return str(target)

        self.mock_client.download_media.side_effect = fake_download_media

        success = SnapshotImporter.rehydrate_from_message(
            client=self.mock_client,
            message=msg,
            db=self.db,
            owner=self.user,
        )

        self.assertTrue(success)

        # Verificar que la DB fue rehidratada
        source = Source.get_or_none(Source.md5sum == "hash_doc_12345")
        assert source is not None
        self.assertIsNotNone(source)
        self.assertEqual(source.path_str, "documento.pdf")

        job = Job.get_or_none(Job.source == source)
        assert job is not None
        self.assertIsNotNone(job)
        self.assertTrue(job.is_completed_local)
        self.assertEqual(job.snapshot_message_id, 101)

        payloads = list(Payload.select().where(Payload.job == job))
        self.assertEqual(len(payloads), 1)

        remotes = list(
            RemotePayload.select().where(RemotePayload.payload == payloads[0])
        )
        self.assertEqual(len(remotes), 1)
        self.assertEqual(remotes[0].message_id, 7701)

    def test_rehydrate_from_message_download_failure(self):
        """Si la descarga desde Telegram falla, rehydrate_from_message debe retornar False limpiamente."""
        msg = self._create_mock_tg_message(102, "invalido.json.xz")
        self.mock_client.download_media.return_value = None  # Fallo de descarga

        success = SnapshotImporter.rehydrate_from_message(
            client=self.mock_client,
            message=msg,
            db=self.db,
            owner=self.user,
        )

        self.assertFalse(success)
        self.assertEqual(Source.select().count(), 0)
        self.assertEqual(Job.select().count(), 0)

    def test_rehydrate_from_message_corrupted_file(self):
        """Si el archivo descargado está corrupto, no debe lanzar excepción no controlada."""
        msg = self._create_mock_tg_message(103, "corrupto.json.xz")

        def fake_download_corrupted(message, file_name):
            target = Path(file_name)
            target.write_bytes(b"contenido binario basura no xz")
            return str(target)

        self.mock_client.download_media.side_effect = fake_download_corrupted

        success = SnapshotImporter.rehydrate_from_message(
            client=self.mock_client,
            message=msg,
            db=self.db,
            owner=self.user,
        )

        self.assertFalse(success)
        self.assertEqual(Source.select().count(), 0)

    # PRUEBAS DE REHIDRATACIÓN EN LOTE (BATCH / INDEXER)

    def test_rehydrate_from_messages_batch_with_mixed_results(self):
        """Procesa un lote mixto: 2 mensajes válidos y 1 fallido, reportando progreso."""
        snap1 = self._create_synthetic_snapshot_file("video1.mp4", "hash_vid_1")
        snap2 = self._create_synthetic_snapshot_file("video2.mp4", "hash_vid_2")

        msg1 = self._create_mock_tg_message(201, "video1.mp4.json.xz")
        msg_fail = self._create_mock_tg_message(202, "fallo.json.xz")
        msg2 = self._create_mock_tg_message(203, "video2.mp4.json.xz")

        def fake_download_batch(message, file_name):
            target = Path(file_name)
            if message.id == 201:
                target.write_bytes(snap1.read_bytes())
                return str(target)
            elif message.id == 203:
                target.write_bytes(snap2.read_bytes())
                return str(target)
            return None  # Mensaje 202 falla la descarga

        self.mock_client.download_media.side_effect = fake_download_batch

        progress_calls = []

        def on_progress(cur, tot, name):
            progress_calls.append((cur, tot, name))

        result: RehydrationResult = SnapshotImporter.rehydrate_from_messages(
            client=self.mock_client,
            messages=[msg1, msg_fail, msg2],
            db=self.db,
            owner=self.user,
            on_progress=on_progress,
        )

        self.assertEqual(result.succeeded_count, 2)
        self.assertEqual(result.total_processed, 3)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("fallo.json.xz", result.errors[0][0])
        self.assertTrue(result.has_changes)

        # Validar que los 2 válidos están en SQLite
        self.assertEqual(Source.select().count(), 2)
        self.assertEqual(Job.select().count(), 2)

    # PRUEBAS DE LA POLÍTICA DE VERSIONES (SnapshotInspector)

    def test_snapshot_inspector_version_evaluation(self):
        """Verifica que SnapshotInspector discrimine entre v6.0, versiones viejas y futuras."""
        # A: Versión actual canónica (v6.0) -> VALID
        snap_v6 = self._create_synthetic_snapshot_file(
            "actual.bin", "hash_act", manifest_version="6.0"
        )
        insp_v6 = SnapshotInspector.inspect_file(snap_v6)
        self.assertEqual(insp_v6.status, InspectionStatus.VALID)

        # B: Versión anterior (ej. v5.0) -> Incompatible (NEEDS_UPGRADE)
        snap_v5 = self._create_synthetic_snapshot_file(
            "viejo.bin", "hash_old", manifest_version="5.0"
        )
        insp_v5 = SnapshotInspector.inspect_file(snap_v5)
        assert insp_v5.error_detail is not None
        self.assertEqual(insp_v5.status, InspectionStatus.NEEDS_UPGRADE)
        self.assertIn("incompatible", insp_v5.error_detail.lower())
        self.assertNotIn("rama de migración", insp_v5.error_detail.lower())

        # C: Versión futura (ej. v7.0) -> Estado AHEAD (esperado correctamente)
        snap_v7 = self._create_synthetic_snapshot_file(
            "futuro.bin", "hash_fut", manifest_version="7.0"
        )
        insp_v7 = SnapshotInspector.inspect_file(snap_v7)
        assert insp_v7.error_detail is not None
        self.assertEqual(insp_v7.status, InspectionStatus.AHEAD)
        self.assertIn("reciente", insp_v7.error_detail.lower())

    def test_import_local_snapshot_auto_links_when_found_in_telegram(self):
        """Valida que un snapshot local consulte Telegram y enlace su RemoteSnapshot si ya existía."""
        valid_md5 = (
            "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"  # 32 caracteres hexadecimales reales
        )
        snap_file = self._create_synthetic_snapshot_file("auto_link.pdf", valid_md5)

        # Simular que Telegram encuentra el mensaje con el caption correcto
        from totelegram.telegram.captions import ManifestCaption

        mock_caption = ManifestCaption(
            resource_name="auto_link.pdf",
            total_size=2048,
            total_parts=1,
            source_short_hash=valid_md5,
            is_tape=False,
        ).render()

        mock_found_msg = self._create_mock_tg_message(909, "auto_link.pdf.json.xz")
        mock_found_msg.caption = mock_caption
        self.mock_client.search_messages.return_value = [mock_found_msg]

        inspection = SnapshotInspector.inspect_file(snap_file)
        plan = SnapshotImportPlanner.plan([inspection])

        succeeded, _ = SnapshotImporter.apply_plan(
            plan=plan,
            db=self.db,
            owner=self.user,
            client=self.mock_client,
        )

        self.assertEqual(succeeded, 1)
        job = Job.get()
        # Debe haber enlazado el ID 909 de Telegram a través de RemoteSnapshot
        self.assertIsNotNone(job.remote_snapshot)
        self.assertEqual(job.snapshot_message_id, 909)


if __name__ == "__main__":
    unittest.main()
