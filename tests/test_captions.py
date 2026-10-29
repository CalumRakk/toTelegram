import unittest

from totelegram.telegram.captions import (
    ChunkCaption,
    ManifestCaption,
    format_version_tag,
)

MAX_TELEGRAM_CAPTION_LENGTH = 1024


class TestCaptions(unittest.TestCase):
    def test_chunk_caption_structure_and_limits(self):
        long_name = (
            "carpeta_con_un_nombre_extremadamente_largo_que_supera_cualquier_limite_normal_"
            * 5
            + ".tar.01-10"
        )
        master_md5 = "4f5ab2c1e8d4a6b7c8d9e0f1a2b3c4d5"

        chunk = ChunkCaption(
            filename=long_name,
            part_number=3,
            total_parts=10,
            part_size=2000000000,
            source_short_hash=master_md5,
        )
        caption = chunk.render()

        self.assertLessEqual(len(caption), MAX_TELEGRAM_CAPTION_LENGTH)
        self.assertIn("#totelegram_chunk", caption)
        self.assertIn("#part_03_10", caption)
        self.assertIn(f"#src_{master_md5}", caption)
        self.assertIn("#v6", caption)

    def test_manifest_caption_distinction(self):
        master_md5 = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"

        # Archivo suelto
        file_manifest = ManifestCaption(
            resource_name="pelicula.mkv",
            total_size=4500000000,
            total_parts=3,
            source_short_hash=master_md5,
            is_tape=False,
        )
        file_caption = file_manifest.render()
        self.assertIn("#type_file", file_caption)
        self.assertIn(f"#src_{master_md5}", file_caption)
        self.assertNotIn("#type_tape", file_caption)
        self.assertIn("Total:", file_caption)

        # Carpeta TAR con conteo de archivos
        tape_manifest = ManifestCaption(
            resource_name="mis_fotos",
            total_size=12000000000,
            total_parts=6,
            source_short_hash=master_md5,
            is_tape=True,
            total_files=1542,
        )
        tape_caption = tape_manifest.render()
        self.assertIn("#type_tape", tape_caption)
        self.assertIn(f"#src_{master_md5}", tape_caption)
        self.assertNotIn("#type_file", tape_caption)
        self.assertIn("1,542 files", tape_caption)

    def test_version_tag_sanitization(self):
        """Verifica que ninguna versión decimal genere puntos en el hashtag."""
        self.assertEqual(format_version_tag(6), "#v6")
        self.assertEqual(format_version_tag("6.0"), "#v6")
        self.assertEqual(format_version_tag("v6.2"), "#v6")

    def test_bidirectional_roundtrip_parsing(self):
        """Verifica que parse() pueda recuperar exactamente los datos de un render()."""
        # Chunk
        chunk = ChunkCaption(
            filename="backup.tar.02-04",
            part_number=2,
            total_parts=4,
            part_size=1024,
            source_short_hash="a5dc69e13dabd923d922e55789fbc012",
        )
        rendered_chunk = chunk.render()
        parsed_chunk = ChunkCaption.parse(rendered_chunk)

        self.assertIsNotNone(parsed_chunk)
        self.assertEqual(parsed_chunk.filename, "backup.tar.02-04")
        self.assertEqual(parsed_chunk.part_number, 2)
        self.assertEqual(parsed_chunk.total_parts, 4)
        self.assertEqual(parsed_chunk.source_short_hash, chunk.source_short_hash)

        # Manifest
        manifest = ManifestCaption(
            resource_name="proyecto",
            total_size=500000,
            total_parts=3,
            source_short_hash="b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5",
            is_tape=True,
            total_files=42,
        )
        rendered_manifest = manifest.render()
        parsed_manifest = ManifestCaption.parse(rendered_manifest)

        self.assertIsNotNone(parsed_manifest)
        self.assertEqual(parsed_manifest.resource_name, "proyecto")
        self.assertTrue(parsed_manifest.is_tape)
        self.assertEqual(parsed_manifest.total_files, 42)
        self.assertEqual(parsed_manifest.total_parts, 3)
        self.assertEqual(parsed_manifest.source_short_hash, manifest.source_short_hash)


if __name__ == "__main__":
    unittest.main()
