import tempfile
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile

import tartape

from totelegram.packaging.partitioner import (
    StatelessPartitioner,
    build_payload_names_pure,
)
from totelegram.schemas import SourceType


class TestChunkingNamingAndPartitioning(unittest.TestCase):
    def test_naming_single_file_no_parts(self):
        """Un archivo de una sola parte no debe llevar sufijos .01-01."""
        full, short = build_payload_names_pure(
            path=Path("documento.pdf"),
            source_type=SourceType.FILE,
            md5sum="md5_doc_123456",
            idx=0,
            total=1,
        )
        self.assertEqual(full, "documento.pdf")
        self.assertEqual(short, "md5_doc_123456.pdf")

    def test_naming_multi_part_file_padding(self):
        """Un archivo dividido en 15 partes debe formatearse con padding de 2 dígitos (.01-15)."""
        full, short = build_payload_names_pure(
            path=Path("video.mp4"),
            source_type=SourceType.FILE,
            md5sum="hash_video_abc",
            idx=0,
            total=15,
        )
        self.assertEqual(full, "video.mp4.01-15")
        self.assertEqual(short, "hash_video_abc.mp4.01-15")

        full_last, short_last = build_payload_names_pure(
            path=Path("video.mp4"),
            source_type=SourceType.FILE,
            md5sum="hash_video_abc",
            idx=14,
            total=15,
        )
        self.assertEqual(full_last, "video.mp4.15-15")
        self.assertEqual(short_last, "hash_video_abc.mp4.15-15")

    def test_naming_folder_tar(self):
        """Una carpeta siempre usa extensión .tar en sus volúmenes."""
        full, short = build_payload_names_pure(
            path=Path("mis_fotos"),
            source_type=SourceType.FOLDER,
            md5sum="fingerprint_folder_99999999999999999999",
            idx=1,
            total=3,
        )
        self.assertEqual(full, "mis_fotos.tar.02-03")
        self.assertTrue(short.endswith(".tar.02-03"))

    def test_partition_file_generates_virtual_chunks(self):
        """StatelessPartitioner.partition_file genera la lista correcta de VirtualChunk sin tocar DB."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"A" * 250)
            tmp_path = Path(tmp.name)

        try:
            chunks = StatelessPartitioner.partition_file(
                path=tmp_path,
                max_chunk_size=100,
                md5sum="dummy_md5_hash",
            )

            self.assertEqual(len(chunks), 3)
            self.assertEqual(chunks[0].sequence_index, 0)
            self.assertEqual(chunks[0].start_offset, 0)
            self.assertEqual(chunks[0].end_offset, 100)
            self.assertEqual(chunks[0].size, 100)

            self.assertEqual(chunks[2].sequence_index, 2)
            self.assertEqual(chunks[2].start_offset, 200)
            self.assertEqual(chunks[2].end_offset, 250)
            self.assertEqual(chunks[2].size, 50)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_partition_folder_generates_virtual_chunks(self):
        """StatelessPartitioner.partition_folder genera VirtualChunk sin extraer miembros SQL."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "test_data"
            root.mkdir()
            (root / "a.bin").write_bytes(b"A" * 1500)
            (root / "b.bin").write_bytes(b"B" * 1500)

            tartape.record(root, checksum="md5", overwrite=True)

            chunks = StatelessPartitioner.partition_folder(
                path=root,
                max_chunk_size=1000,
            )

            self.assertGreater(len(chunks), 1)
            self.assertEqual(chunks[0].sequence_index, 0)

            total_parts = len(chunks)
            expected_first = f".tar.01-{total_parts:02d}"
            expected_last = f".tar.{total_parts:02d}-{total_parts:02d}"

            self.assertTrue(
                chunks[0].filename.endswith(expected_first),
                f"Esperado sufijo '{expected_first}', obtenido: '{chunks[0].filename}'",
            )
            self.assertTrue(
                chunks[-1].filename.endswith(expected_last),
                f"Esperado sufijo '{expected_last}', obtenido: '{chunks[-1].filename}'",
            )


if __name__ == "__main__":
    unittest.main()
