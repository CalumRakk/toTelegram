import logging
import os
import sys
import time
import unittest
from pathlib import Path
from typing import List

from totelegram.core.profiles import ProfileManager

sys.path.append(os.getcwd())

from tempfile import TemporaryDirectory

from totelegram.core.enums import JobStatus, Strategy
from totelegram.core.schemas import UploadManifest
from totelegram.core.setting import get_settings
from totelegram.logging_config import setup_logging
from totelegram.orchestrator import upload
from totelegram.store.database import init_database
from totelegram.store.models import Job, RemotePayload, SourceFile, db_proxy
from totelegram.telegram import init_telegram_client


class TestSendFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Path("tests/logs").mkdir(parents=True, exist_ok=True)
        setup_logging("tests/logs/test_runs.log", logging.INFO)

    @classmethod
    def tearDownClass(cls):
        logging.shutdown()

    def setUp(self):
        self.pm = ProfileManager()

        self.media_folder = TemporaryDirectory()

        profile_name= "demo"
        if profile_name is None:
            raise RuntimeError("No hay perfil activo para ejecutar las pruebas.")

        path = self.pm.get_profile_path(profile_name)
        self.settings = get_settings(path)
        self.settings.database_name= "test.sqlite"
        init_database(self.settings)

    def tearDown(self):
        logger = logging.getLogger(__name__)
        logger.info("Cerrando y limpiando base de datos de prueba")
        self.media_folder.cleanup()
        db_proxy.close()

        if self.settings.database_path.exists():
            try:
                self.settings.database_path.unlink(missing_ok=True)
            except PermissionError:
                logger.warning("No se pudo borrar la BD (archivo en uso), ignorando...")

    def _create_dummy_file(self, size_mb: int) -> Path:
        """Crea un archivo temporal de tamaño específico para pruebas controladas"""
        filename = f"dummy_{size_mb}MB.bin"
        path = Path(self.media_folder.name) / filename
        if not path.exists():
            with open(path, "wb") as f:
                f.write(os.urandom(size_mb * 1024 * 1024))
        return path

    def _remove_messages_from_manifests(self, manifests: List[UploadManifest]):
        """Elimina los archivos subidos a Telegram usando la info del manifiesto"""
        if not manifests:
            return

        logger = logging.getLogger(__name__)
        client = init_telegram_client(self.settings)

        to_remove = {}

        for manifest in manifests:
            for part in manifest.parts:
                chat_id = part.chat_id
                if chat_id not in to_remove:
                    to_remove[chat_id] = []
                to_remove[chat_id].append(part.message_id)

        for chat_id, messages_ids in to_remove.items():
            try:
                logger.info(f"Borrando {len(messages_ids)} mensajes del chat {chat_id}")
                client.delete_messages(chat_id, messages_ids)  # type: ignore
            except Exception as e:
                logger.error(f"Error borrando mensajes: {e}")

    def test_upload_single_file(self):
        """Prueba la subida de un archivo pequeño (Estrategia SINGLE)"""

        self.target = self._create_dummy_file(1)

        result = [i for i in upload(target=self.target, settings=self.settings)]

        self.assertTrue(
            len(result) > 0, "Debería haber retornado al menos un manifiesto"
        )
        manifest = result[0]

        # Verificar en Base de Datos
        # Buscamos el Job asociado al archivo
        source = SourceFile.get(SourceFile.md5sum == manifest.source.md5sum)
        job = Job.get(Job.source == source)

        with self.subTest("Estrategia correcta"):
            self.assertEqual(job.strategy, Strategy.SINGLE)
            self.assertEqual(manifest.strategy, Strategy.SINGLE)

        with self.subTest("Estado del Job"):
            self.assertEqual(job.status, JobStatus.UPLOADED)

        with self.subTest("Verificar partes remotas"):
            self.assertEqual(len(manifest.parts), 1)
            # Verificar que existe en la tabla RemotePayload
            remote = RemotePayload.get(
                RemotePayload.message_id == manifest.parts[0].message_id
            )
            self.assertIsNotNone(remote)

        self._remove_messages_from_manifests(result)

    def test_upload_pieces_file(self):
        """Prueba la subida de un archivo partido (Estrategia CHUNKED)"""
        target_size_mb = 5
        self.target = self._create_dummy_file(target_size_mb)

        file_size = self.target.stat().st_size

        # Forzamos que el tamaño máximo sea menor al archivo (ej: 2MB)
        # Esto obligará a partirlo en 3 partes (2MB, 2MB, 1MB)
        self.settings.max_filesize_bytes = 2 * 1024 * 1024

        result = [i for i in upload(target=self.target, settings=self.settings)]

        self.assertTrue(len(result) > 0)
        manifest = result[0]

        source = SourceFile.get(SourceFile.md5sum == manifest.source.md5sum)
        job = Job.get(Job.source == source)

        with self.subTest("Estrategia correcta"):
            self.assertEqual(job.strategy, Strategy.CHUNKED)
            self.assertEqual(manifest.strategy, Strategy.CHUNKED)

        with self.subTest("Número de partes"):
            # 5MB / 2MB = 3 partes
            self.assertEqual(len(manifest.parts), 3)
            self.assertEqual(job.payloads.count(), 3)  # type: ignore

        self._remove_messages_from_manifests(result)

    def test_upload_throttled(self):
        """Test para verificar que el límite de velocidad funciona"""
        file_size_mb = 2
        dummy_path = self._create_dummy_file(file_size_mb)

        # Configurar límite: 500 KB/s
        # Teoría: 2048 KB total / 500 KB/s = ~4.1 segundos mínimo
        limit_kbps = 500
        self.settings.upload_limit_rate_kbps = limit_kbps

        logger = logging.getLogger(__name__)
        logger.info(f"Iniciando test de velocidad: {file_size_mb}MB a {limit_kbps}KB/s")

        start_time = time.time()
        result = [i for i in upload(target=dummy_path, settings=self.settings)]
        end_time = time.time()

        duration = end_time - start_time
        logger.info(f"Subida completada en {duration:.2f} segundos")

        # El tiempo esperado ideal es (Size / Speed).
        expected_min_seconds = (file_size_mb * 1024) / limit_kbps

        # Aserto de tiempo con margen de tolerancia (0.8x)
        self.assertGreaterEqual(
            duration,
            expected_min_seconds * 0.8,
            f"La subida fue demasiado rápida ({duration}s). El límite de {limit_kbps}KB/s no parece haber funcionado.",
        )
        self._remove_messages_from_manifests(result)


if __name__ == "__main__":
    unittest.main()
