import tempfile
import unittest
from pathlib import Path

import tartape
from tartape.schemas import FileSlice

from totelegram.packaging.partitioner import StatelessPartitioner


class TestTapeMemberMath(unittest.TestCase):
    def test_calculate_file_slices_matches_partitioner_fragments(self):
        """Verifica que calculate_file_slices() produce exactamente las mismas
        coordenadas que el catálogo TarTape usando el start_offset del VirtualTapeMember."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "test_folder"
            root.mkdir()

            # 1. Crear archivos con casos límite respecto a un chunk_size de 10 KB (10240 bytes)
            # - Archivo pequeño: Cabe entero en Volumen 0
            (root / "small.txt").write_bytes(b"A" * 500)

            # - Archivo cruzado: Empieza en Volumen 0 y termina en Volumen 1
            (root / "cross.dat").write_bytes(b"B" * 12000)

            # - Archivo gigante: Cruza Volumen 1, llena Volumen 2 entero y termina en Volumen 3
            (root / "giant.bin").write_bytes(b"C" * 25000)

            # - Archivo vacío: 0 bytes (no debe tener contenido ni fragmentos de datos)
            (root / "empty.txt").write_bytes(b"")

            # 2. Registrar la cinta con tartape
            tape = tartape.record(root, checksum="md5", overwrite=True)

            # 3. Particionar usando el motor de toTelegram con chunks de 10 KB (10240 bytes)
            chunk_size = 10 * 1024
            _, _, virtual_members = StatelessPartitioner.partition_folder(
                root, max_chunk_size=chunk_size, exclude_patterns=[]
            )

            self.assertGreater(len(virtual_members), 0)

            # 4. Comprobación 1:1 para cada archivo
            for vm in virtual_members:
                # Caso archivo vacío (0 bytes)
                if vm.size == 0:
                    slices = tartape.calculate_file_slices(
                        start_offset=vm.start_offset,
                        file_size=vm.size,
                        volume_size=chunk_size,
                    )
                    self.assertEqual(len(slices), 0)
                    continue

                # Cálculo matemático O(1) con TarTape
                calculated_slices: list[FileSlice] = tartape.calculate_file_slices(
                    start_offset=vm.start_offset,
                    file_size=vm.size,
                    volume_size=chunk_size,
                )

                # Coordenadas esperadas resueltas por TarTape para este layout
                expected_gps = tape.locate(vm.relative_path, tag_or_size=chunk_size)

                # Debe haber exactamente la misma cantidad de partes
                self.assertEqual(
                    len(calculated_slices),
                    len(expected_gps.fragments),
                    f"Discrepancia en cantidad de fragmentos para '{vm.relative_path}': "
                    f"Matemática={len(calculated_slices)}, GPS={len(expected_gps.fragments)}",
                )

                # Comparación campo por campo
                for idx, (calc, frag) in enumerate(
                    zip(calculated_slices, expected_gps.fragments)
                ):
                    self.assertEqual(
                        calc.volume_index,
                        frag.volume_index,
                        f"Fallo en {vm.relative_path} parte {idx}: volume_index {calc.volume_index} != {frag.volume_index}",
                    )
                    self.assertEqual(
                        calc.volume_offset,
                        frag.volume_offset,
                        f"Fallo en {vm.relative_path} parte {idx}: volume_offset {calc.volume_offset} != {frag.volume_offset}",
                    )
                    self.assertEqual(
                        calc.volume_length,
                        frag.volume_length,
                        f"Fallo en {vm.relative_path} parte {idx}: volume_length {calc.volume_length} != {frag.volume_length}",
                    )
                    self.assertEqual(
                        calc.source_offset,
                        frag.source_offset,
                        f"Fallo en {vm.relative_path} parte {idx}: source_offset {calc.source_offset} != {frag.source_offset}",
                    )

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
