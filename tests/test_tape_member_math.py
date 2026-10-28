import tempfile
import unittest
from pathlib import Path

import tartape
from tartape.schemas import FileSlice

from totelegram.packaging.partitioner import StatelessPartitioner


class TestTapeMemberMath(unittest.TestCase):
    def test_calculate_file_slices_matches_partitioner_fragments(self):
        """Verifica que calculate_file_slices() produce exactamente las mismas
        coordenadas que el catálogo TarTape usando el start_offset de las pistas."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "test_folder"
            root.mkdir()

            # Crear archivos con casos límite respecto a un chunk_size de 10 KB (10240 bytes)
            (root / "small.txt").write_bytes(b"A" * 500)
            (root / "cross.dat").write_bytes(b"B" * 12000)
            (root / "giant.bin").write_bytes(b"C" * 25000)
            (root / "empty.txt").write_bytes(b"")

            # Registrar la cinta con tartape
            tape = tartape.record(root, checksum="md5", overwrite=True)

            chunk_size = 10 * 1024
            chunks = StatelessPartitioner.partition_folder(
                root, max_chunk_size=chunk_size
            )
            self.assertGreater(len(chunks), 0)

            # Obtener las pistas del catálogo de la cinta para validar la matemática
            with (
                tartape.open_catalog(tape.catalog_path, read_only=True) as cat,
                cat.db_session,
            ):
                cursor = cat.db_session.db.execute_sql(
                    "SELECT arc_path, size, checksum, start_offset FROM track WHERE is_dir = 0"
                )
                tracks = cursor.fetchall()

            self.assertGreater(len(tracks), 0)

            for arc_path, size, checksum, start_offset in tracks:
                if size == 0:
                    slices = tartape.calculate_file_slices(
                        start_offset=start_offset or 0,
                        file_size=size,
                        volume_size=chunk_size,
                    )
                    self.assertEqual(len(slices), 0)
                    continue

                calculated_slices: list[FileSlice] = tartape.calculate_file_slices(
                    start_offset=start_offset or 0,
                    file_size=size,
                    volume_size=chunk_size,
                )

                expected_gps = tape.locate(arc_path, tag_or_size=chunk_size)

                self.assertEqual(
                    len(calculated_slices),
                    len(expected_gps.fragments),
                    f"Discrepancia en cantidad de fragmentos para '{arc_path}': "
                    f"Matemática={len(calculated_slices)}, GPS={len(expected_gps.fragments)}",
                )

                for idx, (calc, frag) in enumerate(
                    zip(calculated_slices, expected_gps.fragments)
                ):
                    self.assertEqual(calc.volume_index, frag.volume_index)
                    self.assertEqual(calc.volume_offset, frag.volume_offset)
                    self.assertEqual(calc.volume_length, frag.volume_length)
                    self.assertEqual(calc.source_offset, frag.source_offset)

    def test_empty_file_slice_projection(self):
        """Archivos de 0 bytes no deben generar fragmentos de corte."""
        slices = tartape.calculate_file_slices(
            start_offset=1000,
            file_size=0,
            volume_size=1024,
        )
        self.assertEqual(len(slices), 0)


if __name__ == "__main__":
    unittest.main()
