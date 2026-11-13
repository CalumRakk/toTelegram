import hashlib
import io
import os
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import tartape
from tartape.stream import ReadAheadStream

from totelegram.common.streams import ThrottledFile
from totelegram.engine.factory import PayloadStreamFactory
from totelegram.models import Job, Payload, Source
from totelegram.schemas import SourceType


class TestUploaderBufferIntegration(unittest.TestCase):
    """Batería de pruebas de integración para el buffer de lectura anticipada (ReadAheadStream)

    en el pipeline de subida de toTelegram.
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

        # 1. Crear un archivo binario representativo (2 MB)
        self.test_file = self.tmp_path / "sample.bin"
        self.payload_bytes = b"BUFFERED_UPLOAD_DATA_BLOCK_" * (2 * 1024 * 1024 // 27)
        self.test_file.write_bytes(self.payload_bytes)
        self.expected_file_md5 = hashlib.md5(self.payload_bytes).hexdigest()

        # 2. Crear una carpeta con múltiples archivos para probar backup (TAR tape)
        self.test_folder = self.tmp_path / "backup_dataset"
        self.test_folder.mkdir()
        (self.test_folder / "file1.txt").write_text("Hello from file 1")
        (self.test_folder / "file2.bin").write_bytes(b"\xaa\xbb\xcc\xdd" * 512)
        (self.test_folder / "nested").mkdir()
        (self.test_folder / "nested" / "file3.txt").write_text("Nested content")

        # Grabar la cinta en tartape
        self.tape = tartape.record(self.test_folder, overwrite=True)

    def tearDown(self):
        self.tape.close()
        self.tmp_dir.cleanup()

    def _build_mock_payload_for_file(
        self, start_offset: int = 0, length: int | None = None
    ) -> tuple[Payload, MagicMock]:
        """Helper para construir un mock consistente de Payload y Job para un archivo suelto."""
        size = length if length is not None else len(self.payload_bytes)
        mock_job = MagicMock(spec=Job)
        mock_source = MagicMock(spec=Source)
        mock_source.type = SourceType.FILE
        mock_source.short_hash = "abc123filehash"
        mock_job.source = mock_source
        mock_job.chunk_size = 2000 * 1024 * 1024
        mock_job.payloads.count.return_value = 1

        mock_payload = MagicMock(spec=Payload)
        mock_payload.job = mock_job
        mock_payload.sequence_index = 0
        mock_payload.filename = "sample.bin"
        mock_payload.filename_short = "sample.bin"
        mock_payload.start_offset = start_offset
        mock_payload.end_offset = start_offset + size
        mock_payload.size = size
        return mock_payload, mock_job

    def _build_mock_payload_for_folder(self) -> tuple[Payload, MagicMock]:
        """Helper para construir un mock consistente de Payload y Job para una carpeta (backup)."""
        mock_job = MagicMock(spec=Job)
        mock_source = MagicMock(spec=Source)
        mock_source.type = SourceType.FOLDER
        mock_source.short_hash = self.tape.fingerprint[:32]
        mock_job.source = mock_source
        mock_job.chunk_size = self.tape.total_size
        mock_job.payloads.count.return_value = 1

        mock_payload = MagicMock(spec=Payload)
        mock_payload.job = mock_job
        mock_payload.sequence_index = 0
        mock_payload.filename = "backup_dataset.tar.001"
        mock_payload.filename_short = "backup.001"
        mock_payload.start_offset = 0
        mock_payload.end_offset = self.tape.total_size
        mock_payload.size = self.tape.total_size
        return mock_payload, mock_job

    # --- 1. PRUEBAS DE ARCHIVO SUELTO (SEND) ---

    def test_file_stream_with_buffer_matches_raw_data_and_hash(self):
        """Verifica que un archivo suelto envuelto en ReadAheadStream se lea íntegro

        y preserve su hash MD5 en vuelo.
        """
        payload, _ = self._build_mock_payload_for_file()
        upload_stream = PayloadStreamFactory.create_stream(
            payload=payload,
            source_path=self.test_file,
            buffer_size_bytes=1024 * 1024,  # Buffer de 1 MB
        )

        self.assertIsInstance(upload_stream.stream, ReadAheadStream)

        with upload_stream:
            received = bytearray()
            while chunk := upload_stream.stream.read(64 * 1024):
                received.extend(chunk)

            self.assertEqual(bytes(received), self.payload_bytes)

        # El hash MD5 debe estar calculado y coincidir con el del archivo original
        self.assertEqual(upload_stream.md5sum, self.expected_file_md5)

    def test_buffer_disabled_returns_raw_file_volume(self):
        """Con buffer_size_bytes=0, no debe usarse ReadAheadStream."""
        payload, _ = self._build_mock_payload_for_file()
        upload_stream = PayloadStreamFactory.create_stream(
            payload=payload,
            source_path=self.test_file,
            buffer_size_bytes=0,
        )
        self.assertNotIsInstance(upload_stream.stream, ReadAheadStream)

    # --- 2. PRUEBAS DE CARPETA TAR (BACKUP) ---

    def test_folder_backup_volume_with_buffer_produces_valid_tar(self):
        """Verifica que empaquetar una carpeta con ReadAheadStream produzca un TAR válido

        cuyos contenidos puedan extraerse e inspeccionarse con tarfile estándar.
        """
        payload, _ = self._build_mock_payload_for_folder()
        upload_stream = PayloadStreamFactory.create_stream(
            payload=payload,
            source_path=self.test_folder,
            buffer_size_bytes=1024 * 1024,
        )

        self.assertIsInstance(upload_stream.stream, ReadAheadStream)

        tar_bytes = bytearray()
        with upload_stream:
            while chunk := upload_stream.stream.read(128 * 1024):
                tar_bytes.extend(chunk)

        self.assertEqual(len(tar_bytes), self.tape.total_size)

        # Desempaquetar con tarfile para asegurar que los bytes son 100% canónicos
        tar_buffer = io.BytesIO(tar_bytes)
        with tarfile.open(fileobj=tar_buffer, mode="r:") as tf:
            names = tf.getnames()
            self.assertTrue(any("file1.txt" in n for n in names))
            self.assertTrue(any("file2.bin" in n for n in names))
            self.assertTrue(any("file3.txt" in n for n in names))

            # Validar contenido interno de un archivo extraído
            f1 = tf.extractfile(next(n for n in names if "file1.txt" in n))
            assert f1 is not None
            self.assertEqual(f1.read().decode("utf-8"), "Hello from file 1")

    # --- 3. PATRÓN "CURIOUS READER" DE PYROGRAM ---

    def test_pyrogram_curious_reader_inspection_pattern(self):
        """Simula exactamente la secuencia de inspección que hace Pyrogram en patches.py:

        1. seek(0, os.SEEK_END)
        2. tell()
        3. seek(0)
        4. seek(resume_offset)
        5. read(...) en bucle
        """
        payload, _ = self._build_mock_payload_for_file()
        upload_stream = PayloadStreamFactory.create_stream(
            payload=payload,
            source_path=self.test_file,
            buffer_size_bytes=1024 * 1024,
        )

        with upload_stream:
            stream = upload_stream.stream

            # Paso 1 y 2: Inspeccionar tamaño al final
            stream.seek(0, os.SEEK_END)
            detected_size = stream.tell()
            self.assertEqual(detected_size, len(self.payload_bytes))

            # Paso 3: Volver al inicio
            stream.seek(0, os.SEEK_SET)
            self.assertEqual(stream.tell(), 0)

            # Paso 4: Reanudar desde un offset arbitrario (ej. byte 1024)
            resume_offset = 1024
            stream.seek(resume_offset, os.SEEK_SET)
            self.assertEqual(stream.tell(), resume_offset)

            # Paso 5: Leer hasta el final (aquí despierta el worker de prefetch)
            resumed_data = bytearray()
            while chunk := stream.read(512 * 1024):
                resumed_data.extend(chunk)

            expected_suffix = self.payload_bytes[resume_offset:]
            self.assertEqual(bytes(resumed_data), expected_suffix)

    # --- 4. COEXISTENCIA CON THROTTLEDFILE (CONTROL DE VELOCIDAD) ---

    def test_throttled_file_wrapping_read_ahead_stream(self):
        """Verifica la jerarquía: Disco -> ReadAheadStream -> ThrottledFile -> Consumidor.

        Asegura que ThrottledFile limite la salida sin desincronizar la lectura anticipada.
        """
        payload, _ = self._build_mock_payload_for_file()
        upload_stream = PayloadStreamFactory.create_stream(
            payload=payload,
            source_path=self.test_file,
            buffer_size_bytes=512 * 1024,
        )

        # Límite deliberadamente alto para el test para no ralentizar la suite (50 MB/s)
        throttled = ThrottledFile(
            upload_stream.stream, speed_limit_bytes_per_s=50 * 1024 * 1024
        )

        with upload_stream, throttled:
            received = bytearray()
            while chunk := throttled.read(256 * 1024):
                received.extend(chunk)

            self.assertEqual(bytes(received), self.payload_bytes)

    # --- 5. CANCELACIÓN Y CIERRE TEMPRANO (RESILIENCIA) ---

    def test_early_close_terminates_background_thread_cleanly(self):
        """Verifica que cerrar el stream a mitad de subida (ej. por FloodWait o Ctrl+C)

        detenga el hilo lector inmediatamente sin dejarlo colgado.
        """
        payload, _ = self._build_mock_payload_for_file()
        upload_stream = PayloadStreamFactory.create_stream(
            payload=payload,
            source_path=self.test_file,
            buffer_size_bytes=1024 * 1024,
        )

        with upload_stream:
            # Consumir un solo bloque para arrancar el worker
            first_chunk = upload_stream.stream.read(64 * 1024)
            self.assertEqual(len(first_chunk), 64 * 1024)

            # Obtener referencia al hilo
            buffered_instance = upload_stream.stream
            assert isinstance(buffered_instance, ReadAheadStream)
            worker_thread = buffered_instance._worker_thread
            self.assertIsNotNone(worker_thread)
            assert worker_thread is not None
            self.assertTrue(worker_thread.is_alive())

        # Al salir del bloque with, close() debe haber detenido el hilo
        time.sleep(0.05)
        self.assertFalse(worker_thread.is_alive())
        self.assertTrue(buffered_instance._closed)

    # --- 6. PROPAGACIÓN DE ERRORES DE I/O ---

    def test_underlying_disk_error_propagates_to_foreground_read(self):
        """Si ocurre un error en el hilo lector secundario (ej. disco desconectado),

        debe propagarse de inmediato al siguiente read() en el hilo principal.
        """

        class FailingStream(io.BufferedIOBase):
            def __init__(self):
                self._pos = 0

            def read(self, size=-1):
                if self._pos > 0:
                    raise OSError("Input/output error: USB device disconnected")
                self._pos += 512
                return b"X" * 512

            def seek(self, offset, whence=io.SEEK_SET):
                return 0

            def tell(self):
                return self._pos

        failing_base = FailingStream()
        buffered = ReadAheadStream(
            failing_base, buffer_size=128 * 1024, block_size=64 * 1024
        )

        with buffered:
            with self.assertRaises(OSError) as ctx:
                while _chunk := buffered.read(1024):
                    pass

            self.assertIn("USB device disconnected", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
