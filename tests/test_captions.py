import unittest

from totelegram.schemas import SourceType
from totelegram.telegram.captions import (
    MAX_TELEGRAM_CAPTION_LENGTH,
    build_chunk_caption,
    build_manifest_caption,
)


class TestCaptions(unittest.TestCase):
    def test_chunk_caption_structure_and_limits(self):
        long_name = (
            "carpeta_con_un_nombre_extremadamente_largo_que_supera_cualquier_limite_normal_"
            * 5
            + ".tar.01-10"
        )
        master_md5 = "4f5ab2c1e8d4a6b7c8d9e0f1a2b3c4d5"

        caption = build_chunk_caption(
            filename=long_name,
            sequence_index=2,
            total_parts=10,
            part_size=2000000000,
            master_md5=master_md5,
            part_md5="d41d8cd98f00b204e9800998ecf8427e",
        )

        self.assertLessEqual(len(caption), MAX_TELEGRAM_CAPTION_LENGTH)
        self.assertIn("#totelegram_chunk", caption)
        self.assertIn("#part_03_10", caption)
        self.assertIn(f"#src_{master_md5}", caption)
        self.assertIn("Parte [3/10]", caption)

    def test_manifest_caption_distinction(self):
        master_md5 = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"

        # Archivo suelto
        file_caption = build_manifest_caption(
            filename="pelicula.mkv",
            source_type=SourceType.FILE,
            total_size=4500000000,
            total_parts=3,
            master_md5=master_md5,
        )
        self.assertIn("#type_file", file_caption)
        self.assertIn(f"#src_{master_md5}", file_caption)
        self.assertNotIn("#type_tape", file_caption)

        # Carpeta TAR
        tape_caption = build_manifest_caption(
            filename="mis_fotos",
            source_type=SourceType.FOLDER,
            total_size=12000000000,
            total_parts=6,
            master_md5=master_md5,
        )
        self.assertIn("#type_tape", tape_caption)
        self.assertIn(f"#src_{master_md5}", tape_caption)
        self.assertNotIn("#type_file", tape_caption)


if __name__ == "__main__":
    unittest.main()
