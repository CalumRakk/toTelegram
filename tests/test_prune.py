import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

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

runner = CliRunner()


class TestPruneAndDirectoryStatus(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.temp_dir.name) / "archivos"
        self.workdir.mkdir()

        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.chat = TelegramChat.create(
            id=-100111111, title="Canal Prune", type="channel"
        )
        self.user = TelegramUser.create(id=1, first_name="PruneUser", is_premium=False)

        self.file_1 = self.workdir / "video1.mp4"
        self.file_1.write_bytes(b"DUMMY_VIDEO_1" * 100)

        self.file_2 = self.workdir / "video2.mp4"
        self.file_2.write_bytes(b"DUMMY_VIDEO_2" * 100)

    def tearDown(self):
        self.db_session.close()
        self.temp_dir.cleanup()

    def _register_file(self, file_path: Path):
        stat = file_path.stat()
        source = Source.create(
            path_str=str(file_path.resolve()),
            md5sum=f"hash_{file_path.name}",
            size=stat.st_size,
            mtime=stat.st_mtime,
            mimetype="video/mp4",
            type=SourceType.FILE,
        )
        job = Job.create(source=source, chat=self.chat, chunk_size=2000)
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
            chat=self.chat,
            owner=self.user,
            json_metadata={"id": 50},
        )
        RemoteSnapshot.register(job, 51, self.chat, self.user)
        return job

    def test_audit_detects_fresh_and_unbacked_files(self):
        # file_1 está respaldado en DB, file_2 no
        self._register_file(self.file_1)

        results = LocalAuditService.audit_path(self.workdir)
        self.assertEqual(len(results), 2)

        res_map = {r.filename: r for r in results}
        self.assertTrue(res_map["video1.mp4"].is_safe_to_prune)
        self.assertFalse(res_map["video2.mp4"].is_safe_to_prune)

    def test_audit_protects_modified_files(self):
        self._register_file(self.file_1)

        # Modificamos file_1 localmente
        time.sleep(0.05)
        self.file_1.write_bytes(b"CONTENIDO_NUEVO_MODIFICADO")

        results = LocalAuditService.audit_path(self.workdir)
        res_map = {r.filename: r for r in results}

        self.assertEqual(res_map["video1.mp4"].status, AuditStatus.MODIFIED)
        self.assertFalse(res_map["video1.mp4"].is_safe_to_prune)

    def test_prune_deletes_file_and_preserves_db_records(self):
        """Verifica que prune borre el archivo físico pero conserve el registro en SQLite."""
        self._register_file(self.file_1)

        # Simulamos ejecución CLI con DB activa
        with patch("totelegram.cli.commands.prune.CLIState"):
            # Poda directa invocando la lógica de auditoría segura
            candidates = [
                item
                for item in LocalAuditService.audit_path(self.workdir)
                if item.is_safe_to_prune
            ]
            self.assertEqual(len(candidates), 1)

            # Borrado físico
            for c in candidates:
                assert c.path is not None
                c.path.unlink()

        # El archivo pesado se eliminó del disco
        self.assertFalse(self.file_1.exists())
        # El archivo no respaldado sigue existiendo
        self.assertTrue(self.file_2.exists())

        # En SQLite el Source y Job siguen existiendo intactos
        source = Source.get_or_none(Source.path_str == str(self.file_1.resolve()))
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.jobs.count(), 1)

        # El siguiente audit lo reporta como REMOTE_ONLY (Solo en Telegram)
        refreshed_audit = LocalAuditService.audit_path(self.workdir)
        res_map = {r.filename: r for r in refreshed_audit}
        self.assertEqual(res_map["video1.mp4"].status, AuditStatus.REMOTE_ONLY)


if __name__ == "__main__":
    unittest.main()
