import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import tartape

from totelegram.packaging.schemas import (
    MANIFEST_VERSION,
    TTAPE_MANIFEST_FILENAME,
    RemotePart,
    SourceMetadata,
    UploadManifest,
)
from totelegram.packaging.ttape import (
    InvalidTapeContainerError,
    TapeContainer,
)
from totelegram.schemas import SourceType


class TestTapeContainer(unittest.TestCase):
    """Pruebas del contenedor híbrido ZIP .ttape (primitivas de archivo, catálogo y manifiesto)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.chat_id = -10099887766

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_tape_container_build_and_read_manifest_o1(self):
        """Valida que TapeContainer empaquete el ZIP y permita leer manifest.json sin descomprimir SQLite."""
        folder = self.base_path / "proyecto_codigo"
        folder.mkdir()
        (folder / "main.py").write_text("print('hello world')")
        (folder / "config.json").write_text('{"debug": true}')

        tape = tartape.record(folder, checksum="md5", overwrite=True)
        self.assertTrue(tape.catalog_path.exists())

        manifest = UploadManifest(
            manifest_version=MANIFEST_VERSION,
            created_at=datetime.now(timezone.utc),
            chunk_size=1024 * 1024,
            chat_id=self.chat_id,
            owner_id=11223344,
            owner_name="TapeTester",
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
                    chat_id=self.chat_id,
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

        built_path = TapeContainer.build(
            output_path=ttape_dest,
            manifest=manifest,
            catalog_path=tape.catalog_path,
            overwrite=True,
        )

        self.assertTrue(built_path.exists())
        self.assertTrue(TapeContainer.is_valid(built_path))

        loaded_manifest = TapeContainer.read_manifest(built_path)
        self.assertEqual(loaded_manifest.manifest_version, MANIFEST_VERSION)
        self.assertEqual(loaded_manifest.source.filename, "proyecto_codigo")
        self.assertEqual(len(loaded_manifest.parts), 1)
        self.assertEqual(loaded_manifest.parts[0].message_id, 501)

        info = TapeContainer.inspect(built_path)
        self.assertGreater(info.total_container_bytes, 0)
        self.assertGreater(info.catalog_compressed_bytes, 0)
        self.assertGreater(info.catalog_uncompressed_bytes, 0)

        with TapeContainer.open_catalog(built_path) as cat:
            self.assertEqual(cat.fingerprint, tape.fingerprint)
            self.assertEqual(cat.file_count, 3)

            gps_main = cat.locate("proyecto_codigo/main.py")
            self.assertIsNotNone(gps_main)
            self.assertEqual(gps_main.file_size, len("print('hello world')"))

    def test_tape_container_invalid_zip_raises_error(self):
        """Valida que un archivo dañado o sin los artefactos requeridos lance InvalidTapeContainerError."""
        corrupt_file = self.base_path / "corrupto.ttape"

        corrupt_file.write_bytes(b"esto no es un zip")
        self.assertFalse(TapeContainer.is_valid(corrupt_file))
        with self.assertRaises(InvalidTapeContainerError):
            TapeContainer.read_manifest(corrupt_file)

        corrupt_file.unlink()
        with zipfile.ZipFile(corrupt_file, "w") as zf:
            zf.writestr(TTAPE_MANIFEST_FILENAME, "{}")

        self.assertFalse(TapeContainer.is_valid(corrupt_file))
        with self.assertRaises(InvalidTapeContainerError):
            TapeContainer.extract_catalog(corrupt_file, self.base_path / "out.tartape")


if __name__ == "__main__":
    unittest.main()
