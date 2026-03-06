import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, cast

from totelegram.common.utils import get_user_config_dir
from totelegram.logic.chunker import ChunkingService
from totelegram.manager.registry import SettingsManager

sys.path.append(os.getcwd())

if TYPE_CHECKING:
    from pyrogram.types import User, Chat

from totelegram.common.enums import AvailabilityState
from totelegram.logic.discovery import DiscoveryService
from totelegram.logic.uploader import UploadService
from totelegram.manager.database import DatabaseSession
from totelegram.manager.models import Job, RemotePayload, Source, TelegramChat
from totelegram.telegram.client import TelegramSession


class TestManualRealLogic(unittest.TestCase):
    """
    Test manual con cuenta real que valida los Escenarios de LOGIC_EXPLAINER.md
    """

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.manager = SettingsManager(self.base_path)
        self.database_path = self.base_path / "test.db"

        # Configuraciones de produccion para telegram
        pdt_profile_name = "demo"
        pdt_dir = get_user_config_dir()
        pdt_manager = SettingsManager(pdt_dir)
        self.pdt_settings = pdt_manager.get_settings("demo")

        if not self.pdt_settings:
            raise unittest.SkipTest("No hay un perfil para ejecutar pruebas.")

        self.db_session = DatabaseSession(self.database_path)
        self.db_session.start()

        self.tg_session = TelegramSession.from_profile(pdt_profile_name, pdt_manager)
        self.client = self.tg_session.start()

    def tearDown(self):
        self.tg_session.stop()
        self.db_session.close()
        self.temp_dir.cleanup()

    def _create_dummy_file(self, name: str, size_mb: int) -> Path:
        path = self.base_path / name
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
        chunker = ChunkingService(work_dir=self.database_path)
        uploader = UploadService(
            client=self.client,
            chunk_service=chunker,
            upload_limit_rate_kbps=0,
            max_filename_length=50,
            discovery=discovery,
        )

        tg_chat = cast("Chat", self.client.get_chat(self.pdt_settings.chat_id))
        chat_db, _ = TelegramChat.get_or_create_from_chat(tg_chat)

        me = cast("User", self.client.get_me())

        # --- CASO SYSTEM_NEW: el archivo es nuevo ----
        target = self._create_dummy_file("video_boda.mp4", 1)
        source = Source.get_or_create_from_filepath(target)

        tg_limit = 4 * (1024**2) if me.is_premium else 2 * (1024**2)
        job = Job.create_contract(source, chat_db, me.is_premium, tg_limit)

        report = discovery.investigate(job)
        self.assertEqual(
            report.state, AvailabilityState.SYSTEM_NEW, "Debería ser un archivo nuevo."
        )

        uploader.execute_physical_upload(job)
        self.assertTrue(job.status == "UPLOADED")

        # --- CASO FULFILLED: el archivo se repite en el mismo chat ---

        report_fulfilled = discovery.investigate(job)
        self.assertEqual(
            report_fulfilled.state,
            AvailabilityState.FULFILLED,
            "Debería detectar integridad local.",
        )

        # --- CASO REMOTE_MIRROR: el archivo se repite en otro chat ---

        # Creamos un chat ficticio en la DB para pedirle al sistema que suba el mismo archivo allí
        chat_virtual, _ = TelegramChat.get_or_create(
            id=999999, defaults={"title": "Virtual", "type": "private"}
        )
        # El Job nace "vacío" (sin payloads en DB), solo con el contrato.
        job_mirror = Job.create_contract(source, chat_virtual, me.is_premium, tg_limit)

        report_mirror = discovery.investigate(job_mirror)
        self.assertEqual(report_mirror.state, AvailabilityState.REMOTE_MIRROR)

        self._cleanup_remote_messages(job)

    def _cleanup_remote_messages(self, job):
        """Elimina los mensajes de Telegram para mantener el chat limpio."""
        remotes = (
            RemotePayload.select()
            .join(Job, on=(RemotePayload.payload_id == Job.id))
            .where(Job.source == job.source)
        )
        msg_ids = [
            r.message_id for r in remotes if r.chat_id == self.pdt_settings.chat_id
        ]
        if msg_ids:
            self.client.delete_messages(self.pdt_settings.chat_id, msg_ids)  # type: ignore


if __name__ == "__main__":
    unittest.main()
