import tempfile
import time
import unittest
from pathlib import Path

from typer.testing import CliRunner

from totelegram.cli.__main__ import app
from totelegram.packaging.ledger import LEDGER_FILENAME, ReceiptLedger

runner = CliRunner()


class TestPruneAndDirectoryStatus(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.temp_dir.name) / "archivos"
        self.workdir.mkdir()

        self.file_1 = self.workdir / "video1.mp4"
        self.file_1.write_bytes(b"DUMMY_VIDEO_1" * 100)

        self.file_2 = self.workdir / "video2.mp4"
        self.file_2.write_bytes(b"DUMMY_VIDEO_2" * 100)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_audit_detects_fresh_and_unbacked_files(self):
        # file_1 está respaldado
        ReceiptLedger.record_upload(
            file_path=self.file_1,
            chat_id=-100111111,
            snapshot_message_id=50,
            chunk_size=2000,
            total_parts=1,
            md5="hash_v1",
            chat_title="Canal Test",
        )

        results = ReceiptLedger.audit_directory(self.workdir)
        self.assertEqual(len(results), 2)

        res_map = {r.filename: r for r in results}
        self.assertTrue(res_map["video1.mp4"].is_safe_to_prune)
        self.assertFalse(res_map["video2.mp4"].is_safe_to_prune)

    def test_audit_protects_modified_files(self):
        ReceiptLedger.record_upload(
            file_path=self.file_1,
            chat_id=-100111111,
            snapshot_message_id=50,
            chunk_size=2000,
            total_parts=1,
            md5="hash_v1",
        )

        # Modificamos file_1 localmente
        time.sleep(0.05)
        self.file_1.write_bytes(b"CONTENIDO_NUEVO_MODIFICADO")

        results = ReceiptLedger.audit_directory(self.workdir)
        res_map = {r.filename: r for r in results}

        # Ya NO debe ser seguro borrarlo
        self.assertTrue(res_map["video1.mp4"].is_modified)
        self.assertFalse(res_map["video1.mp4"].is_safe_to_prune)

    def test_prune_deletes_backed_up_file_and_keeps_ledger(self):
        ReceiptLedger.record_upload(
            file_path=self.file_1,
            chat_id=-100111111,
            snapshot_message_id=50,
            chunk_size=2000,
            total_parts=1,
            md5="hash_v1",
            chat_title="Canal Test",
        )

        # Ejecutamos prune con --yes
        result = runner.invoke(app, ["prune", str(self.workdir), "--yes"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Liberación completada", result.stdout)

        # El archivo pesado se eliminó
        self.assertFalse(self.file_1.exists())
        # El archivo no respaldado sigue existiendo intacto
        self.assertTrue(self.file_2.exists())
        # El recibo .totelegram.json sigue existiendo
        self.assertTrue((self.workdir / LEDGER_FILENAME).exists())


if __name__ == "__main__":
    unittest.main()
