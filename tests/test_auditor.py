import tempfile
import time
import unittest
from pathlib import Path

from totelegram.database import DatabaseSession
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    RemoteSnapshot,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.auditor import AuditStatus, LocalAuditService
from totelegram.schemas import SourceType


class TestLocalAuditService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.temp_dir.name) / "archivos"
        self.workdir.mkdir()

        # Base de datos SQLite aislada en memoria
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.chat = TelegramChat.create(
            id=-100111222333, title="Canal Auditoría", type="channel"
        )
        self.user = TelegramUser.create(
            id=1, first_name="AuditorUser", is_premium=False
        )

        # Archivo local de prueba
        self.file_a = self.workdir / "video1.mp4"
        self.file_a.write_bytes(b"DATA_VIDEO_1" * 100)

    def tearDown(self):
        self.db_session.close()
        self.temp_dir.cleanup()

    def _register_file_in_db(self, file_path: Path, chat: TelegramChat) -> Job:
        stat = file_path.stat()
        source = Source.create(
            path_str=str(file_path.resolve()),
            md5sum="hash_a1234567890",
            size=stat.st_size,
            mtime=stat.st_mtime,
            mimetype="video/mp4",
            type=SourceType.FILE,
        )
        job = Job.create(source=source, chat=chat, chunk_size=2000)
        p = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=stat.st_size,
            size=stat.st_size,
            filename=file_path.name,
            filename_short=file_path.name,
        )
        RemotePayload.create(
            payload=p,
            message_id=50,
            chat=chat,
            owner=self.user,
            json_metadata={"id": 50},
        )
        RemoteSnapshot.register(job, 51, chat, self.user)
        return job

    def test_audit_unbacked_file_is_pending(self):
        """Un archivo que solo existe en disco aparece como PENDING."""
        items = LocalAuditService.audit_path(self.workdir)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].filename, "video1.mp4")
        self.assertEqual(items[0].status, AuditStatus.PENDING)
        self.assertFalse(items[0].is_safe_to_prune)

    def test_audit_backed_up_file(self):
        """Un archivo en disco con Job y RemoteSnapshot aparece como BACKED_UP."""
        self._register_file_in_db(self.file_a, self.chat)

        items = LocalAuditService.audit_path(self.workdir)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.status, AuditStatus.BACKED_UP)
        self.assertTrue(item.is_safe_to_prune)
        self.assertIn("Canal Auditoría", item.destinations[0])

    def test_audit_protects_modified_file(self):
        """Si el archivo en disco cambia de tamaño o mtime, pasa a MODIFIED."""
        self._register_file_in_db(self.file_a, self.chat)

        time.sleep(0.05)
        self.file_a.write_bytes(b"CONTENIDO_TOTALMENTE_NUEVO_Y_MAS_LARGO")

        items = LocalAuditService.audit_path(self.workdir)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.status, AuditStatus.MODIFIED)
        self.assertFalse(item.is_safe_to_prune)

    def test_audit_moved_file_path_auto_update(self):
        """Camino 2: Si el archivo se movió de carpeta, se detecta por (filename, size, mtime) y se actualiza path_str."""
        job = self._register_file_in_db(self.file_a, self.chat)

        nuevo_dir = self.workdir / "subcarpeta"
        nuevo_dir.mkdir()
        nuevo_path = nuevo_dir / "video1.mp4"
        self.file_a.rename(nuevo_path)

        items = LocalAuditService.audit_path(nuevo_dir)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.status, AuditStatus.BACKED_UP)

        source_refreshed = Source.get_by_id(job.source.id)
        self.assertEqual(source_refreshed.path_str, str(nuevo_path.resolve()))

    def test_audit_detects_legacy_snapshot_without_parsing(self):
        """Comprueba que un snapshot .json.xz corrupto o no registrado se detecte sin abrirlo ni fallar."""
        sidecar = self.workdir / f"{self.file_a.name}.json.xz"
        sidecar.write_bytes(b"ESTO NO ES UN ARCHIVO LZMA VALIDO")

        items = LocalAuditService.audit_path(self.workdir)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.status, AuditStatus.LOCAL_SNAPSHOT)
        self.assertIn("Local (Snapshot)", item.destinations)
        self.assertFalse(item.is_safe_to_prune)

    def test_audit_unbacked_folder_is_neutral_directory(self):
        """Una carpeta sin respaldo de cinta aparece como neutral (DIRECTORIO)."""
        sub_dir = self.workdir / "mis_documentos"
        sub_dir.mkdir()

        items = LocalAuditService.audit_path(self.workdir)
        item_dir = next(i for i in items if i.filename == "mis_documentos")
        self.assertEqual(item_dir.status, AuditStatus.DIRECTORY)
        self.assertTrue(item_dir.is_dir)
        self.assertFalse(item_dir.is_safe_to_prune)
        self.assertEqual(len(item_dir.destinations), 0)

    def test_audit_tape_folder_backup(self):
        """Una carpeta registrada como cinta TAR aparece como TAPE_BACKUP."""
        sub_dir = self.workdir / "fotos_vacaciones"
        sub_dir.mkdir()

        source_folder = Source.create(
            path_str=str(sub_dir.resolve()),
            md5sum="fingerprint_folder_abc",
            size=5000,
            mtime=1700000000.0,
            mimetype="application/x-tar",
            type=SourceType.FOLDER,
        )
        job = Job.create(source=source_folder, chat=self.chat, chunk_size=2000)
        p = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=5000,
            size=5000,
            filename="fotos.tar.01-01",
            filename_short="f.tar.01-01",
        )
        RemotePayload.create(
            payload=p,
            message_id=60,
            chat=self.chat,
            owner=self.user,
            json_metadata={"id": 60},
        )
        RemoteSnapshot.register(job, 61, self.chat, self.user)

        items = LocalAuditService.audit_path(self.workdir)
        item_folder = next(i for i in items if i.filename == "fotos_vacaciones")
        self.assertEqual(item_folder.status, AuditStatus.TAPE_BACKUP)
        self.assertTrue(item_folder.is_dir)
        self.assertIn("Canal Auditoría", item_folder.destinations[0])

    def test_audit_remote_only_file(self):
        """Un archivo registrado en DB que ya fue borrado de disco (prune) aparece como REMOTE_ONLY."""
        self._register_file_in_db(self.file_a, self.chat)
        self.file_a.unlink()

        items = LocalAuditService.audit_path(self.workdir)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.status, AuditStatus.REMOTE_ONLY)
        self.assertIsNone(item.path)
        self.assertFalse(item.is_safe_to_prune)

    def test_prune_deletes_file_and_preserves_db_records(self):
        """Verifica que al podar se borre el archivo físico pero se conserve el registro en SQLite."""
        self._register_file_in_db(self.file_a, self.chat)

        # Archivo secundario no respaldado para confirmar que prune no lo toca
        file_unbacked = self.workdir / "no_respaldado.mp4"
        file_unbacked.write_bytes(b"DATA_NO_RESPALDADA")

        # Localizar candidatos seguros para liberar espacio
        candidates = [
            item
            for item in LocalAuditService.audit_path(self.workdir)
            if item.is_safe_to_prune
        ]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].filename, "video1.mp4")

        # Borrado físico simulado
        for c in candidates:
            assert c.path is not None
            c.path.unlink()

        # El archivo respaldado se borró físicamente
        self.assertFalse(self.file_a.exists())
        # El archivo no respaldado sigue en su lugar intacto
        self.assertTrue(file_unbacked.exists())

        # En SQLite el Source y Job siguen existiendo intactos
        source = Source.get_or_none(Source.path_str == str(self.file_a.resolve()))
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.jobs.count(), 1)

        # La siguiente auditoría lo detecta como REMOTE_ONLY
        refreshed_audit = LocalAuditService.audit_path(self.workdir)
        res_map = {r.filename: r for r in refreshed_audit}
        self.assertEqual(res_map["video1.mp4"].status, AuditStatus.REMOTE_ONLY)


if __name__ == "__main__":
    unittest.main()
