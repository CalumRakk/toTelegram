import logging
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from totelegram.core.plans import SkipPlan
from totelegram.services.validator import ValidationService

sys.path.append(os.getcwd())

from totelegram.core.enums import AvailabilityState, DuplicatePolicy
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import get_settings
from totelegram.logging_config import setup_logging
from totelegram.services.discovery import DiscoveryService
from totelegram.services.policy import PolicyExpert
from totelegram.services.uploader import UploadService
from totelegram.store.database import DatabaseSession
from totelegram.store.models import Job, RemotePayload, SourceFile, TelegramChat
from totelegram.telegram import TelegramSession


class TestManualRealLogic(unittest.TestCase):
    """
    Test manual con cuenta real que valida los Escenarios de LOGIC_EXPLAINER.md
    """

    @classmethod
    def setUpClass(cls):
        Path("tests/logs").mkdir(parents=True, exist_ok=True)
        setup_logging("tests/logs/test_runs.log", logging.INFO)
        cls.logger = cast(logging.Logger, logging.getLogger("ManualReal"))

        cls.pm = ProfileManager()
        profile_name = "demo"
        if not profile_name:
            raise unittest.SkipTest("No hay un perfil activo para ejecutar pruebas.")

        cls.logger.info(f"=== INICIANDO SUITE REAL: PERFIL {profile_name} ===")

        env_path = cls.pm.get_path(profile_name)
        cls.settings = get_settings(env_path)
        cls.settings.database_name = "manual_test_run.sqlite"
        cls.media_folder = TemporaryDirectory()

        cls.db_session = DatabaseSession(cls.settings)
        cls.db_session.__enter__()

        cls.tg_session = TelegramSession(cls.settings)
        cls.client = cls.tg_session.start()

        validate = ValidationService()
        validate._force_refresh_peers(cls.client)

    @classmethod
    def tearDownClass(cls):
        cls.tg_session.stop()
        cls.db_session.__exit__(None, None, None)
        cls.media_folder.cleanup()
        if cls.settings.database_path.exists():
            try:
                cls.settings.database_path.unlink()
            except:
                pass

    def _create_dummy_file(self, name: str, size_mb: int) -> Path:
        path = Path(self.media_folder.name) / name
        with open(path, "wb") as f:
            f.write(os.urandom(size_mb * 1024 * 1024))
        return path

    def test_logic_scenarios(self):
        """
        Prueba los estados SYSTEM_NEW -> FULFILLED -> REMOTE_MIRROR
        usando los servicios reales.
        """
        # Preparación de servicios
        discovery = DiscoveryService(self.client)
        uploader = UploadService(self.client, self.settings)
        from pyrogram.types import Chat

        tg_chat = self.client.get_chat(self.settings.chat_id)
        chat_db = TelegramChat.get_or_create_from_tg(cast(Chat, tg_chat))
        from pyrogram.types import User

        me = cast(User, self.client.get_me())

        # --- CASO SYSTEM_NEW: el archivo es nuevo ----
        self.logger.info(">>> TEST 1: Subida física inicial (SYSTEM_NEW)")
        target = self._create_dummy_file("video_boda.mp4", 1)
        source = SourceFile.get_or_create_from_path(target)

        job = Job.create_contract(source, chat_db, me.is_premium, self.settings)

        report = discovery.investigate(job)
        self.assertEqual(
            report.state, AvailabilityState.SYSTEM_NEW, "Debería ser un archivo nuevo."
        )

        uploader.execute_physical_upload(job)
        self.assertTrue(job.status == "UPLOADED")
        self.logger.info("✔ Archivo subido físicamente.")

        # --- CASO FULFILLED: el archivo se repite en el mismo chat ---
        self.logger.info(">>> TEST 2: Intento repetido en el mismo chat (FULFILLED)")

        report_fulfilled = discovery.investigate(job)
        self.assertEqual(
            report_fulfilled.state,
            AvailabilityState.FULFILLED,
            "Debería detectar integridad local.",
        )

        plan = PolicyExpert.determine_plan(report_fulfilled, DuplicatePolicy.SMART)
        self.logger.info(f"Plan decidido: {plan}")
        self.assertIsInstance(plan, SkipPlan)
        self.logger.info("El sistema sabe que el archivo existe en el mismo chat.")

        # --- CASO REMOTE_MIRROR: el archivo se repite en otro chat ---
        self.logger.info(">>> TEST 3: Detección de duplicidad global (MIRROR)")

        # Creamos un chat ficticio en la DB para pedirle al sistema que suba el mismo archivo allí
        chat_virtual, _ = TelegramChat.get_or_create(
            id=999999, defaults={"title": "Virtual", "type": "private"}
        )
        # El Job nace "vacío" (sin payloads en DB), solo con el contrato.
        job_mirror = Job.create_contract(
            source, chat_virtual, me.is_premium, self.settings
        )

        report_mirror = discovery.investigate(job_mirror)
        self.assertEqual(report_mirror.state, AvailabilityState.REMOTE_MIRROR)
        self.logger.info(
            "El sistema sabe que el archivo existe en otro chat del ecosistema."
        )

        self._cleanup_remote_messages(job)

    def _cleanup_remote_messages(self, job):
        """Elimina los mensajes de Telegram para mantener el chat limpio."""
        remotes = (
            RemotePayload.select()
            .join(Job, on=(RemotePayload.payload_id == Job.id))
            .where(Job.source == job.source)
        )
        msg_ids = [r.message_id for r in remotes if r.chat_id == self.settings.chat_id]
        if msg_ids:
            self.logger.info(f"Limpiando {len(msg_ids)} mensajes del test...")
            self.client.delete_messages(self.settings.chat_id, msg_ids)  # type: ignore


if __name__ == "__main__":
    unittest.main()
