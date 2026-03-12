import hashlib
import os
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile

from totelegram.stream import FileVolume


class TestFileVolumeIntegrity(unittest.TestCase):
    def setUp(self):
        self.data = os.urandom(1024 * 1024)
        self.temp_file = NamedTemporaryFile(delete=False)
        self.temp_file.write(self.data)
        self.temp_file.close()
        self.path = Path(self.temp_file.name)
        self.expected_md5 = hashlib.md5(self.data).hexdigest()

    def tearDown(self):
        if self.path.exists():
            os.remove(self.path)

    def test_linear_read_success(self):
        """Lectura lineal perfecta."""
        volume = FileVolume(self.path, 0, len(self.data), "linear.bin")
        with volume:
            content = volume.read()
            self.assertEqual(hashlib.md5(content).hexdigest(), self.expected_md5)
            self.assertFalse(volume._integrity_broken)
            self.assertEqual(volume.md5sum, self.expected_md5)

    def test_the_curious_reader(self):
        """Simula librería que va al final (metadatos) y vuelve al inicio."""
        volume = FileVolume(self.path, 0, len(self.data), "curious.bin")
        with volume:
            volume.seek(0, os.SEEK_END)  # Va al final
            volume.read(1)  # Intenta leer (0 bytes)
            volume.seek(0, os.SEEK_SET)  # Vuelve al inicio

            content = volume.read()  # Lectura real

            self.assertFalse(
                volume._integrity_broken,
                "El seek al final NO debería romper la integridad",
            )
            self.assertEqual(volume.md5sum, self.expected_md5)

    def test_overlapping_rewind_retry(self):
        """
        Simula reintento de Pyrogram.
        Lee 0-100, luego lee 100-200, luego retrocede a 150 y lee hasta el final.
        """
        volume = FileVolume(self.path, 0, len(self.data), "retry.bin")
        with volume:
            p1 = volume.read(100)
            p2 = volume.read(100)
            # Retrocede 50 bytes (simula fallo de red y reintento)
            volume.seek(150)
            # Lee el resto
            p3 = volume.read()

            # El contenido total leído por el consumidor sería p1 + p2 + (parte de p3)
            # Pero el MD5 interno debe ser capaz de ignorar el solapamiento.
            self.assertFalse(
                volume._integrity_broken,
                "Un retroceso (Rewind) no debería romper la integridad",
            )
            self.assertEqual(volume.md5sum, self.expected_md5)

    def test_broken_integrity_gap(self):
        """
        Salto hacia adelante.
        Lee 0-100 y luego salta a 200-300. Esto SÍ debe romper la integridad lineal.
        """
        volume = FileVolume(self.path, 0, len(self.data), "gap.bin")
        with volume:
            volume.read(100)
            volume.seek(200)  # Saltamos 100 bytes sin leerlos
            volume.read(100)

            self.assertTrue(
                volume._integrity_broken,
                "Un salto hacia adelante DEBE romper la integridad lineal",
            )
            # A pesar de romperse, el fallback manual debe devolver el MD5 correcto
            self.assertEqual(volume.md5sum, self.expected_md5)

    def test_partial_read_triggers_manual(self):
        """
        El consumidor nunca termina de leer el volumen.
        El hash cursor no llegará al final, debe disparar manual.
        """
        volume = FileVolume(self.path, 0, len(self.data), "partial.bin")
        with volume:
            volume.read(500)

            # El cursor de hash estará en 500, pero el tamaño es 1MB.
            # md5sum debe notar que no está completo y calcularlo manualmente.
            self.assertEqual(volume.md5sum, self.expected_md5)


if __name__ == "__main__":
    unittest.main()
