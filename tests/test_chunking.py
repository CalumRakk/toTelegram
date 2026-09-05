import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile

from totelegram.packaging.partitioner import (
    StatelessPartitioner,
    build_payload_names_pure,
    chunk_ranges,
)
from totelegram.schemas import SourceType


class TestChunkingMath(unittest.TestCase):
    def test_chunk_ranges_exact_division(self):
        """Caso: 10MB archivo, 5MB chunk -> 2 partes exactas"""
        file_size = 10 * 1024 * 1024
        chunk_size = 5 * 1024 * 1024
        ranges = chunk_ranges(file_size, chunk_size)

        self.assertEqual(len(ranges), 2)
        self.assertEqual(ranges[0], (0, 5242880))
        self.assertEqual(ranges[1], (5242880, 10485760))

    def test_chunk_ranges_remainder(self):
        """Caso: 10 bytes archivo, 3 bytes chunk -> 4 partes (3, 3, 3, 1)"""
        ranges = chunk_ranges(10, 3)
        expected = [(0, 3), (3, 6), (6, 9), (9, 10)]
        self.assertEqual(ranges, expected)

    def test_chunk_ranges_file_smaller_than_chunk(self):
        """Archivo menor que el límite -> 1 sola parte."""
        ranges = chunk_ranges(file_size=500, chunk_size=1000)
        self.assertEqual(ranges, [(0, 500)])

    # --- PRUEBAS DE NOMBRADO PURO (build_payload_names_pure) ---

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

        # Probar la última parte
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
            # Chunk 0
            self.assertEqual(chunks[0].sequence_index, 0)
            self.assertEqual(chunks[0].start_offset, 0)
            self.assertEqual(chunks[0].end_offset, 100)
            self.assertEqual(chunks[0].size, 100)

            # Chunk 2 (Resto de 50 bytes)
            self.assertEqual(chunks[2].sequence_index, 2)
            self.assertEqual(chunks[2].start_offset, 200)
            self.assertEqual(chunks[2].end_offset, 250)
            self.assertEqual(chunks[2].size, 50)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
