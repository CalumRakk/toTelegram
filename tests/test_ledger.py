import json
import tempfile
import time
import unittest
from pathlib import Path

from totelegram.packaging.ledger import (
    LEDGER_FILENAME,
    DirectoryReceipt,
    ReceiptLedger,
)


class TestReceiptLedger(unittest.TestCase):
    def setUp(self):
        """Crea un entorno de prueba temporal y archivos de muestra."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.temp_dir.name) / "videos"
        self.workdir.mkdir()

        self.file_a = self.workdir / "video1.mp4"
        self.file_a.write_bytes(b"DATA_VIDEO_1" * 100)

        self.file_b = self.workdir / "video2.mp4"
        self.file_b.write_bytes(b"DATA_VIDEO_2" * 50)

    def tearDown(self):
        """Limpia el directorio temporal."""
        self.temp_dir.cleanup()

    def test_load_nonexistent_returns_empty_receipt(self):
        receipt = ReceiptLedger.load(self.workdir)

        self.assertIsInstance(receipt, DirectoryReceipt)
        self.assertEqual(receipt.version, 1)
        self.assertEqual(len(receipt.entries), 0)

    def test_record_upload_and_atomic_save(self):
        chat_id = -100123456789

        receipt = ReceiptLedger.record_upload(
            file_path=self.file_a,
            chat_id=chat_id,
            snapshot_message_id=500,
            chunk_size=2000 * 1024 * 1024,
            total_parts=1,
            md5="fake_md5_hash_1",
            chat_title="Mi Canal",
        )

        # Verificar que el archivo .totelegram.json existe en disco
        ledger_path = self.workdir / LEDGER_FILENAME
        self.assertTrue(ledger_path.exists())

        # Verificar contenido deserializado
        self.assertIn("video1.mp4", receipt.entries)
        entry = receipt.entries["video1.mp4"]
        self.assertEqual(entry.md5, "fake_md5_hash_1")
        self.assertIn(str(chat_id), entry.destinations)

        dest = entry.destinations[str(chat_id)]
        self.assertEqual(dest.snapshot_message_id, 500)
        self.assertEqual(dest.chat_title, "Mi Canal")

    def test_is_backed_up_check(self):
        chat_1 = -100111111111
        chat_2 = -100222222222

        ReceiptLedger.record_upload(
            file_path=self.file_a,
            chat_id=chat_1,
            snapshot_message_id=10,
            chunk_size=2000,
            total_parts=1,
            md5="hash_a",
        )

        # file_a está respaldado en chat_1
        self.assertTrue(ReceiptLedger.is_backed_up(self.file_a, chat_1))

        # file_a NO está respaldado en chat_2
        self.assertFalse(ReceiptLedger.is_backed_up(self.file_a, chat_2))

        # file_b NO está respaldado en ningún chat
        self.assertFalse(ReceiptLedger.is_backed_up(self.file_b, chat_1))

    def test_multi_channel_coexistence(self):
        """Prueba que el mismo archivo puede tener múltiples destinos registrados sin colisión."""
        chat_personal = -100111111111
        chat_backup = -100222222222

        ReceiptLedger.record_upload(
            file_path=self.file_a,
            chat_id=chat_personal,
            snapshot_message_id=101,
            chunk_size=2000,
            total_parts=1,
            md5="hash_a",
            chat_title="Canal Personal",
        )

        ReceiptLedger.record_upload(
            file_path=self.file_a,
            chat_id=chat_backup,
            snapshot_message_id=202,
            chunk_size=4000,
            total_parts=1,
            md5="hash_a",
            chat_title="Canal Backup",
        )

        # Ambos destinos coexisten en el mismo archivo
        receipt = ReceiptLedger.load(self.workdir)
        destinations = receipt.entries["video1.mp4"].destinations

        self.assertEqual(len(destinations), 2)
        self.assertEqual(destinations[str(chat_personal)].snapshot_message_id, 101)
        self.assertEqual(destinations[str(chat_backup)].snapshot_message_id, 202)

        self.assertTrue(ReceiptLedger.is_backed_up(self.file_a, chat_personal))
        self.assertTrue(ReceiptLedger.is_backed_up(self.file_a, chat_backup))

    def test_mtime_invalidation(self):
        """Si el archivo local es modificado después de la subida, se invalida el respaldo."""
        chat_id = -100123456789

        ReceiptLedger.record_upload(
            file_path=self.file_a,
            chat_id=chat_id,
            snapshot_message_id=100,
            chunk_size=2000,
            total_parts=1,
            md5="hash_original",
        )

        self.assertTrue(ReceiptLedger.is_backed_up(self.file_a, chat_id))

        # Modificar el archivo local (cambia mtime y size)
        time.sleep(0.05)
        self.file_a.write_bytes(b"DATA_MODIFICADA_NUEVA")

        # Ahora el ledger debe detectar que no está al día
        self.assertFalse(ReceiptLedger.is_backed_up(self.file_a, chat_id))

    def test_corrupted_ledger_recovery(self):
        """Si el archivo .totelegram.json se corrompe en disco, load() no debe crashear."""
        ledger_file = self.workdir / LEDGER_FILENAME
        ledger_file.write_text("{ esto es un json roto !!!", encoding="utf-8")

        receipt = ReceiptLedger.load(self.workdir)
        self.assertIsInstance(receipt, DirectoryReceipt)
        self.assertEqual(len(receipt.entries), 0)

    def test_remove_destination(self):
        """Prueba la eliminación de un destino específico."""
        chat_id = -100123456789
        ReceiptLedger.record_upload(
            file_path=self.file_a,
            chat_id=chat_id,
            snapshot_message_id=100,
            chunk_size=2000,
            total_parts=1,
            md5="hash_a",
        )

        self.assertTrue(ReceiptLedger.is_backed_up(self.file_a, chat_id))

        removed = ReceiptLedger.remove_destination(self.file_a, chat_id)
        self.assertTrue(removed)
        self.assertFalse(ReceiptLedger.is_backed_up(self.file_a, chat_id))

    def test_assimilate_modern_v6_snapshot(self):
        """Asimila automáticamente un snapshot .json.xz v6.0 presente en disco."""
        import lzma
        from datetime import datetime, timezone

        from totelegram.packaging.schemas import (
            RemotePart,
            SourceMetadata,
            UploadManifest,
        )
        from totelegram.schemas import SourceType

        chat_id = -100555555555
        manifest = UploadManifest(
            manifest_version="6.0",
            created_at=datetime.now(timezone.utc),
            chunk_size=2000,
            chat_id=chat_id,
            owner_id=1,
            owner_name="Test",
            source=SourceMetadata(
                filename="video1.mp4",
                size=self.file_a.stat().st_size,
                md5sum="fake_md5_hash_1",
                mime_type="video/mp4",
                mtime=self.file_a.stat().st_mtime,
                type=SourceType.FILE,
            ),
            parts=[
                RemotePart(
                    sequence=0,
                    message_id=888,
                    chat_id=chat_id,
                    link="https://t.me/c/1/888",
                    part_filename="video1.mp4",
                    part_size=self.file_a.stat().st_size,
                    part_md5sum="fake_md5_hash_1",
                    start_offset=0,
                    end_offset=self.file_a.stat().st_size,
                )
            ],
        )

        # Crear el sidecar .json.xz
        sidecar = self.workdir / f"{self.file_a.name}.json.xz"
        with lzma.open(sidecar, "wt", encoding="utf-8") as f:
            f.write(manifest.model_dump_json())

        # El ledger aún no existe
        self.assertFalse((self.workdir / LEDGER_FILENAME).exists())

        # is_backed_up debe asimilarlo y crear .totelegram.json
        self.assertTrue(ReceiptLedger.is_backed_up(self.file_a, chat_id))
        self.assertTrue((self.workdir / LEDGER_FILENAME).exists())

        dest = ReceiptLedger.get_destination(self.file_a, chat_id)
        self.assertIsNotNone(dest)
        self.assertEqual(dest.snapshot_message_id, 888)
        self.assertEqual(dest.chunk_size, 2000)

    def test_assimilate_archaeological_snapshot_without_chunk_size(self):
        """Asimila un snapshot antiguo de versiones anteriores que no tenían chunk_size."""
        import lzma

        chat_id = -100777777777
        ancient_json = {
            "config": {
                "chat_id": chat_id,
                "tg_max_size": 1500,
            },
            "source": {
                "size": self.file_a.stat().st_size,
                "md5sum": "ancient_md5_1",
            },
            "parts": [{"message_id": 404, "size": self.file_a.stat().st_size}],
        }

        sidecar = self.workdir / f"{self.file_a.name}.json.xz"
        with lzma.open(sidecar, "wt", encoding="utf-8") as f:
            f.write(json.dumps(ancient_json))

        # is_backed_up debe extraerlo con éxito usando la cascada heurística
        self.assertTrue(ReceiptLedger.is_backed_up(self.file_a, chat_id))
        dest = ReceiptLedger.get_destination(self.file_a, chat_id)
        self.assertIsNotNone(dest)
        self.assertEqual(dest.snapshot_message_id, 404)
        self.assertEqual(dest.chunk_size, 1500)

    def test_heal_directory_from_db(self):
        """Auto-sana y reconstruye el .totelegram.json desde SQLite si fue eliminado."""
        from totelegram.database import DatabaseSession
        from totelegram.models import (
            Job,
            RemoteSnapshot,
            Source,
            TelegramChat,
            TelegramUser,
        )
        from totelegram.schemas import SourceType

        with DatabaseSession("sqlite:///:memory:", auto_init_schema=True) as db:
            chat = TelegramChat.create(
                id=-100999999, title="Chat DB Heal", type="channel"
            )
            user = TelegramUser.create(id=1, first_name="HealUser", is_premium=False)
            source = Source.create(
                path_str=str(self.file_a),
                md5sum="md5_heal_test",
                size=self.file_a.stat().st_size,
                mtime=self.file_a.stat().st_mtime,
                mimetype="video/mp4",
                type=SourceType.FILE,
            )
            job = Job.create(source=source, chat=chat, chunk_size=2000)
            RemoteSnapshot.create(job=job, chat=chat, message_id=7711, owner=user)

            # Asegurar que no existe el archivo de ledger
            ledger_file = self.workdir / LEDGER_FILENAME
            if ledger_file.exists():
                ledger_file.unlink()

            healed_count = ReceiptLedger.heal_directory_from_db(self.workdir, db=db)
            self.assertEqual(healed_count, 1)

            # Ahora debe estar respaldado según el ledger auto-sanado
            self.assertTrue(ReceiptLedger.is_backed_up(self.file_a, chat.id))
            dest = ReceiptLedger.get_destination(self.file_a, chat.id)
            self.assertEqual(dest.snapshot_message_id, 7711)
            self.assertEqual(dest.chat_title, "Chat DB Heal")


if __name__ == "__main__":
    unittest.main()
