import logging
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, cast

sys.path.append(os.getcwd())

from totelegram.core.enums import Strategy
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import get_settings
from totelegram.logging_config import setup_logging
from totelegram.services.chunking import ChunkingService
from totelegram.services.snapshot import SnapshotService
from totelegram.services.uploader import UploadService
from totelegram.store.database import DatabaseSession
from totelegram.store.models import Job, SourceFile
from totelegram.telegram import TelegramSession


class TestManualReal(unittest.TestCase):
    pm: ProfileManager
    media_folder: TemporaryDirectory
    db_session: DatabaseSession
    tg_session: TelegramSession
    client = None

    @classmethod
    def setUpClass(cls):
        """
        Se ejecuta UNA VEZ antes de todos los tests.
        Aquí abrimos la Base de Datos y la Conexión a Telegram.
        """
        Path("tests/logs").mkdir(parents=True, exist_ok=True)
        setup_logging("tests/logs/test_runs.log", logging.INFO)
        cls.logger = cast(logging.Logger, logging.getLogger("TestManualReal"))

        cls.pm = ProfileManager()

        # Resolver Perfil
        profile_name = "demo"
        if not cls.pm.exists(profile_name):
            profile_name = cls.pm.active_name

        if not profile_name:
            raise unittest.SkipTest(
                "No hay perfil 'demo' ni activo para ejecutar pruebas."
            )

        cls.logger.info(f"=== INICIANDO SUITE DE TESTS CON PERFIL: {profile_name} ===")

        # Configurar Entorno
        env_path = cls.pm.get_path(profile_name)
        cls.settings = get_settings(env_path)
        cls.settings.database_name = "test_run.sqlite"

        # Carpeta temporal para generar archivos dummy (dura toda la clase)
        cls.media_folder = TemporaryDirectory()

        # 3. Iniciar Base de Datos (Manual enter)
        cls.db_session = DatabaseSession(cls.settings)
        cls.db_session.__enter__()

        cls.tg_session = TelegramSession(cls.settings)
        try:
            cls.client = cls.tg_session.start()
        except Exception as e:
            cls.logger.critical(f"No se pudo conectar a Telegram: {e}")
            cls.db_session.__exit__(None, None, None)
            raise e

    @classmethod
    def tearDownClass(cls):
        """
        Se ejecuta UNA VEZ al final de todos los tests.
        Cierra conexiones y limpia archivos.
        """
        cls.logger.info("=== FINALIZANDO SUITE DE TESTS ===")

        # Cerrar Telegram
        if cls.tg_session:
            cls.tg_session.stop()

        # Cerrar BD
        if cls.db_session:
            cls.db_session.__exit__(None, None, None)

        # Limpiar carpeta temporal
        if cls.media_folder:
            cls.media_folder.cleanup()

        # Borrar archivo SQLite
        if cls.settings and cls.settings.database_path.exists():
            try:
                cls.settings.database_path.unlink()
            except PermissionError:
                cls.logger.warning("No se pudo borrar la BD temporal (archivo en uso).")

    def _create_dummy_file(
        self, size_mb: int, force_name: Optional[str] = None
    ) -> Path:
        """
        Crea un archivo con datos aleatorios.
        Por defecto genera un nombre único para evitar colisiones de MD5/DB entre tests.
        """
        if force_name:
            filename = force_name
        else:
            timestamp = time.time_ns()
            filename = f"dummy_{size_mb}MB_{timestamp}.bin"

        path = Path(self.media_folder.name) / filename
        with open(path, "wb") as f:
            f.write(os.urandom(size_mb * 1024 * 1024))

        return path

    def _execute_upload_pipeline(self, target_path: Path):
        chunker = ChunkingService(self.settings)
        uploader = UploadService(self.client, self.settings)

        source = SourceFile.get_or_create_from_path(target_path)
        job = Job.get_or_create_from_source(source, self.settings)

        payloads = chunker.process_job(job)
        for payload in payloads:
            uploader.upload_payload(payload)

        job.set_uploaded()
        manifest = SnapshotService.generate_snapshot(job)
        return manifest

    def _remove_messages_from_manifest(self, manifest):
        """Borra los mensajes usando el cliente compartido."""
        if not self.client:
            return

        chat_ids_map = {}
        for part in manifest.parts:
            if part.chat_id not in chat_ids_map:
                chat_ids_map[part.chat_id] = []
            chat_ids_map[part.chat_id].append(part.message_id)

        for chat_id, msg_ids in chat_ids_map.items():
            try:
                self.logger.info(
                    f"Limpieza: Borrando {len(msg_ids)} mensajes en {chat_id}"
                )
                self.client.delete_messages(chat_id, msg_ids)  # type: ignore
            except Exception as e:
                self.logger.error(f"Error borrando mensajes: {e}")

    def test_01_upload_single_file(self):
        """Test subida archivo único (pequeño)"""
        target = self._create_dummy_file(1)
        manifest = self._execute_upload_pipeline(target)

        self.assertEqual(manifest.strategy, Strategy.SINGLE)
        self.assertEqual(len(manifest.parts), 1)

        # Limpieza inmediata para no saturar el chat si falla el siguiente test
        self._remove_messages_from_manifest(manifest)

    def test_02_upload_pieces_file(self):
        """Test subida archivo partido (Chunked)"""

        # Modificar configuración "en caliente" es seguro porque Settings es mutable en memoria
        self.settings.max_filesize_bytes = 2 * 1024 * 1024  # type: ignore

        target = self._create_dummy_file(5)
        manifest = self._execute_upload_pipeline(target)

        self.assertEqual(manifest.strategy, Strategy.CHUNKED)
        self.assertEqual(len(manifest.parts), 3)  # 5MB / 2MB = 3 partes (2, 2, 1)

        self._remove_messages_from_manifest(manifest)

    def test_03_upload_throttled(self):
        """Test limitador de velocidad"""
        file_size_mb = 1
        limit_kbps = 500

        self.settings.upload_limit_rate_kbps = limit_kbps
        # Restaurar tamaño para evitar hacer chunking innecesario
        self.settings.max_filesize_bytes = 2000 * 1024 * 1024

        target = self._create_dummy_file(file_size_mb)

        start = time.time()
        manifest = self._execute_upload_pipeline(target)
        duration = time.time() - start

        expected_min = (file_size_mb * 1024) / limit_kbps
        self.logger.info(
            f"Speed Test: {duration:.2f}s (Mínimo teórico: {expected_min:.2f}s)"
        )

        self.assertGreater(
            duration,
            expected_min * 0.8,
            "Subida fue demasiado rápida, el limitador no funcionó.",
        )
        self._remove_messages_from_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
