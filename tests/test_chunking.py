import unittest

from totelegram.services.chunking import FileChunker


class TestChunkingMath(unittest.TestCase):
    def test_chunk_ranges_exact_division(self):
        """Caso: 10MB archivo, 5MB chunk -> 2 partes exactas"""
        file_size = 10 * 1024 * 1024
        chunk_size = 5 * 1024 * 1024
        ranges = FileChunker._chunk_ranges(file_size, chunk_size)

        self.assertEqual(len(ranges), 2)
        self.assertEqual(ranges[0], (0, 5242880))
        self.assertEqual(ranges[1], (5242880, 10485760))

    def test_chunk_ranges_remainder(self):
        """Caso: 10 bytes archivo, 3 bytes chunk -> 4 partes (3, 3, 3, 1)"""
        ranges = FileChunker._chunk_ranges(10, 3)
        expected = [(0, 3), (3, 6), (6, 9), (9, 10)]
        self.assertEqual(ranges, expected)
