import logging
import os
import sys
import time
import unittest
from pathlib import Path
from typing import List

sys.path.append(os.getcwd())

from totelegram.logging_config import setup_logging
from totelegram.models import File, FileCategory, FileStatus, db_proxy
from totelegram.setting import get_settings
from totelegram.uploader.database import init_database
from totelegram.uploader.handlers import upload
from totelegram.uploader.telegram import init_telegram_client


class TestSendFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_logging("tests/logs/test_runs.log", logging.INFO)

    @classmethod
    def tearDownClass(cls):
        logging.shutdown()

    def setUp(self):
        self.settings = get_settings("env/test.env")
        self.settings.database_name = "test.db"
        self.media_folder = Path("tests") / "medias"
        self.target = (
            self.media_folder / "Otan Mian Anoixi (Live - Bonus Track)-(240p).mp4"
        )

        # Asegurar que existe directorio de logs
        Path("tests/logs").mkdir(parents=True, exist_ok=True)

    def _create_dummy_file(self, size_mb: int) -> Path:
        """Crea un archivo temporal de tamaño específico para pruebas controladas"""
        filename = f"dummy_{size_mb}MB.bin"
        path = self.media_folder / filename
        self.media_folder.mkdir(parents=True, exist_ok=True)

        with open(path, "wb") as f:
            f.write(os.urandom(size_mb * 1024 * 1024))
        return path

    def _remove_messages(self, messages: List):
        """Elimina los archivos subidos a Telegram"""
        logger = logging.getLogger(__name__)
        if not messages:
            return

        to_remove = {}
        for message in messages:
            chat_id = message.chat.id
            if chat_id in to_remove:
                to_remove[chat_id].append(message.id)
            else:
                to_remove[chat_id] = [message.id]

        client = init_telegram_client(self.settings)
        for chat_id, messages_ids in to_remove.items():
            try:
                logger.info(f"Borrando {len(messages_ids)} mensajes del chat {chat_id}")
                client.delete_messages(chat_id, messages_ids)  # type: ignore
            except Exception as e:
                logger.error(f"Error borrando mensajes: {e}")

    def tearDown(self):
        # Limpieza de BD después de cada test
        logger = logging.getLogger(__name__)
        logger.info("Cerrando y limpiando base de datos de prueba")
        db_proxy.close()
        if self.settings.database_path.exists():
            try:
                self.settings.database_path.unlink()
            except PermissionError:
                logger.warning("No se pudo borrar la BD (archivo en uso), ignorando...")

    def test_upload_single_file(self):
        if not self.target.exists():
            self.skipTest(f"Archivo {self.target} no encontrado")

        init_database(self.settings)

        result = upload(target=self.target, settings=self.settings)

        file: File = File.get(File.md5sum == result[0].manager.file.md5sum)

        with self.subTest("resultado tiene un elemento"):
            self.assertEqual(len(result), 1)

        with self.subTest("archivo es de tipo single-file"):
            self.assertEqual(file.type, FileCategory.SINGLE)

        with self.subTest("archivo estí¡ subido"):
            self.assertEqual(file.status, FileStatus.UPLOADED)

        message = file.message_db.get_message()
        self._remove_messages([message])

    def test_upload_pieces_file(self):
        if not self.target.exists():
            self.skipTest(f"Archivo {self.target} no encontrado")

        init_database(self.settings)
        file_size = self.target.stat().st_size

        self.settings.max_filesize_bytes = int(file_size / 2)

        result = upload(target=self.target, settings=self.settings)
        file: File = File.get(File.md5sum == result[0].manager.file.md5sum)

        with self.subTest("archivo es de tipo chunked"):
            self.assertEqual(file.type, FileCategory.CHUNKED)

        messages = [piece.message for piece in file.pieces]
        self._remove_messages(messages)

    def test_upload_throttled(self):
        """Test para verificar que el lí­mite de velocidad funciona"""
        file_size_mb = 2
        dummy_path = self._create_dummy_file(file_size_mb)

        try:
            init_database(self.settings)

            # 2. Configurar lí­mite: 500 KB/s
            # Teorí­a: 2048 KB total / 500 KB/s = ~4.1 segundos mí­nimo
            limit_kbps = 500
            self.settings.upload_limit_rate_kbps = limit_kbps

            logger = logging.getLogger(__name__)
            logger.info(
                f"Iniciando test de velocidad: {file_size_mb}MB a {limit_kbps}KB/s"
            )

            start_time = time.time()
            result = upload(target=dummy_path, settings=self.settings)
            end_time = time.time()

            duration = end_time - start_time
            logger.info(f"Subida completada en {duration:.2f} segundos")

            file: File = File.get(File.md5sum == result[0].manager.file.md5sum)
            message = file.message_db.get_message()
            self._remove_messages([message])

            # El tiempo esperado ideal es (Size / Speed).
            # Le damos un margen de error pequeño (0.8x) por si el buffer inicial es rápido,
            # pero definitivamente no debería ser instantáneo.
            expected_min_seconds = (file_size_mb * 1024) / limit_kbps

            self.assertGreaterEqual(
                duration,
                expected_min_seconds * 0.8,
                f"La subida fue demasiado rápida ({duration}s). El límite de {limit_kbps}KB/s no parece haber funcionado para 2MB.",
            )

        finally:
            if dummy_path.exists():
                dummy_path.unlink()


if __name__ == "__main__":
    unittest.main()
