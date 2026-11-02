import unittest
from datetime import timedelta
from unittest.mock import MagicMock

from totelegram.common.helpers import get_utc_now
from totelegram.database import DatabaseSession
from totelegram.discovery import DiscoveryService
from totelegram.engine.pipeline import JobPipeline
from totelegram.exceptions import ConcurrencyError
from totelegram.models import (
    Claim,
    Job,
    Payload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.schemas import ExecutionDecision, ResourceType, SourceType


class TestConcurrencyCapacityRace(unittest.TestCase):
    def setUp(self):
        # Usamos la sesión canónica del proyecto para inicializar todas las tablas en memoria
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.chat = TelegramChat.create(
            id=-1001234567, title="Test Chat", type="channel"
        )
        self.user_premium = TelegramUser.create(
            id=111, first_name="Premium", is_premium=True
        )
        self.user_normal = TelegramUser.create(
            id=222, first_name="Normal", is_premium=False
        )

        # Archivo simulado de 2.5 GB
        self.source = Source.create(
            path_str="/tmp/test_video.mkv",
            md5sum="abcdef1234567890abcdef1234567890",
            size=2621440000,
            mtime=1700000000.0,
            mimetype="video/x-matroska",
            type=SourceType.FILE,
        )

        # Job creado con 4 GB de corte por cuenta Premium
        self.job = Job.create(source=self.source, chat=self.chat, chunk_size=4194304000)
        self.payload = Payload.create(
            job=self.job,
            sequence_index=0,
            filename="test_video.mkv",
            filename_short="test_video.mkv",
            size=2621440000,
            start_offset=0,
            end_offset=2621440000,
        )

    def tearDown(self):
        self.db_session.close()

    def test_normal_account_without_force_yields_incompatible_capacity_even_with_zero_remotes(
        self,
    ):
        """
        Una cuenta estándar (2 GB) no debe purgar un Job de 4 GB sin partes en Telegram;
        debe reportar INCOMPATIBLE_CAPACITY y detenerse.
        """
        client_mock = MagicMock()
        client_mock.search_messages.return_value = []
        discovery = DiscoveryService(client=client_mock, db=self.db)

        # Cuenta normal con límite de 2 GB
        report = discovery.investigate(
            job=self.job,
            force=False,
            account_max_chunk_size=2097152000,
        )

        self.assertEqual(report.decision, ExecutionDecision.INCOMPATIBLE_CAPACITY)

    def test_force_cannot_purge_job_if_another_node_holds_active_claim(self):
        """
        Incluso con --force, un nodo no puede purgar el Job si otro nodo
        está subiendo activamente una de sus piezas.
        """
        # Simulamos que otro nodo ('node_premium') tiene un lease activo de la pieza
        Claim.create(
            resource_id=f"payload:{self.payload.id}",
            resource_type=ResourceType.PAYLOAD,
            node_id="node_premium",
            expires_at=get_utc_now() + timedelta(seconds=120),
        )

        session_mock = MagicMock()
        session_mock.db = self.db
        session_mock.coordinator.node_id = "node_normal"

        pipeline = JobPipeline(session=session_mock)

        with self.assertRaises(ConcurrencyError) as ctx:
            pipeline._ensure_no_active_claims_on_job(self.job)

        self.assertIn("node_premium", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
