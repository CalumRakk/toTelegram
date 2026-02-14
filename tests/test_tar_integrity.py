import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tartape import TarTape
from totelegram.services.tar_stream import TapeInspector, TarVolume


class TestTarTapeStreaming(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = TemporaryDirectory()
        self.base_path = Path(self.tmp_dir.name)

        self.file_a = self.base_path / "file_a.bin"
        self.file_a.write_bytes(b"A" * 1024 * 512)  # 512KB

        self.file_b = self.base_path / "file_b.bin"
        self.file_b.write_bytes(b"B" * 1024 * 1024)  # 1MB

    def tearDown(self):
        self.tmp_dir.cleanup()

    def get_md5(self, data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def test_reconstruction_integrity(self):
        """
        Prueba que la suma de varios volúmenes pequeños es igual
        al stream completo original.
        """
        tape = TarTape(anonymize=True)
        tape.add_folder(self.base_path)

        # Genera el TAR completo
        full_tar = bytearray()
        for event in tape.stream():
            if event.type.value == "file_data":
                full_tar.extend(event.data)  # type: ignore

        reference_md5 = self.get_md5(full_tar)
        total_size = len(full_tar)

        # Genera volúmenes pequeños (ej. 256KB cada uno)
        VOL_SIZE = 256 * 1024
        reconstructed_tar = bytearray()

        num_volumes = (total_size // VOL_SIZE) + 1
        total_size = TapeInspector.get_total_size(tape)
        for i in range(num_volumes):
            start_offset = i * VOL_SIZE
            stream = TarVolume(
                tape=tape,
                start_offset=start_offset,
                max_volume_size=VOL_SIZE,
                total_tape_size=total_size,
                vol_index=i,
            )

            chunk = stream.read()
            while chunk:
                reconstructed_tar.extend(chunk)
                chunk = stream.read()

        self.assertEqual(
            len(full_tar),
            len(reconstructed_tar),
            "El tamaño del TAR reconstruido no coincide.",
        )
        self.assertEqual(
            reference_md5,
            self.get_md5(reconstructed_tar),
            "El MD5 del TAR reconstruido no coincide. La cinta se corrompió en los cortes.",
        )

    def test_metadata_offsets(self):
        """
        Verifica que el stream reporte correctamente en qué volumen
        quedó guardado cada archivo.
        """
        tape = TarTape(anonymize=True)
        tape.add_folder(self.base_path)

        # Usamos un volumen lo suficientemente grande para que file_a esté en vol 0
        # y file_b cruce al vol 1
        VOL_SIZE = 600 * 1024

        total_tape_size = TapeInspector.get_total_size(tape)

        # Volumen 0
        stream0 = TarVolume(
            tape=tape,
            start_offset=0,
            max_volume_size=VOL_SIZE,
            total_tape_size=total_tape_size,
            vol_index=0,
        )
        while stream0.read(1024 * 64):
            pass
        _, entries0 = stream0.get_completed_files()

        # Volumen 1
        stream1 = TarVolume(
            tape=tape,
            start_offset=VOL_SIZE,
            max_volume_size=VOL_SIZE,
            total_tape_size=total_tape_size,
            vol_index=1,
        )
        while stream1.read(1024 * 64):
            pass
        _, entries1 = stream1.get_completed_files()

        # Unir todos los registros de archivos completados
        all_entries = entries0 + entries1
        paths_encontrados = [e["path"] for e in all_entries]

        self.assertIn(f"{self.base_path.name}/file_a.bin", paths_encontrados)
        self.assertIn(f"{self.base_path.name}/file_b.bin", paths_encontrados)

    def test_reconstruction_is_identical(self):
        """Valida que el TAR reconstruido por piezas es binariamente identico al stream original."""
        tape = TarTape(anonymize=True)
        tape.add_folder(self.base_path)

        # Obtiene stream completo de referencia
        full_reference = bytearray()
        for event in tape.stream():
            if event.type.value == "file_data":
                full_reference.extend(event.data)  # type: ignore

        ref_md5 = hashlib.md5(full_reference).hexdigest()
        total_size = len(full_reference)

        VOL_SIZE = 200 * 1024  # 200KB para asegurar varios cortes
        reconstructed = bytearray()

        num_vols = (total_size // VOL_SIZE) + 1
        for i in range(num_vols):
            stream = TarVolume(
                tape=tape,
                start_offset=i * VOL_SIZE,
                max_volume_size=VOL_SIZE,
                total_tape_size=total_size,
                vol_index=i,
            )

            chunk = stream.read()
            while chunk:
                reconstructed.extend(chunk)
                chunk = stream.read()

        self.assertEqual(len(full_reference), len(reconstructed))
        self.assertEqual(ref_md5, hashlib.md5(reconstructed).hexdigest())


if __name__ == "__main__":
    unittest.main()
