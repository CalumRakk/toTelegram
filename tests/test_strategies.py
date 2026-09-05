import json
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, patch

from totelegram.concurrency import ConcurrencyCoordinator
from totelegram.database import DatabaseSession
from totelegram.engine.events import UploadObserver
from totelegram.engine.strategies.base import StrategyResolver
from totelegram.engine.strategies.forward import SmartForwardStrategy
from totelegram.engine.strategies.fulfilled import FulfilledStrategy
from totelegram.engine.strategies.upload import CooperativeUploadStrategy
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.schemas import (
    AvailabilityState,
    JobStatus,
    SourceType,
    Strategy,
    StrategyConfig,
)
from totelegram.types import AvailabilityReport, UploadContext


class TestEngineStrategies(unittest.TestCase):
    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.mock_client = MagicMock()
        self.mock_observer = MagicMock(spec=UploadObserver)
        self.coordinator = ConcurrencyCoordinator(self.db, node_id="test_worker_node")

        self.chat = TelegramChat.create(
            id=-100123456, title="Canal Principal", type="channel"
        )
        self.chat_mirror = TelegramChat.create(
            id=-100987654, title="Canal Respaldo", type="channel"
        )
        self.user = TelegramUser.create(
            id=999, first_name="StrategyUser", is_premium=False
        )

        self.settings = MagicMock()
        self.settings.chat_id = self.chat.id
        self.settings.telegram_account_id = self.user.id
        self.settings.upload_limit_rate_kbps = 0
        self.settings.max_filename_length = 55
        self.settings.upload_pause_range = [0, 0]
        self.settings.exclude_files = []

        self.ctx = UploadContext(
            tg_chat=MagicMock(id=self.chat.id, title=self.chat.title),
            owner=self.user,
            client=self.mock_client,
            db=self.db,
            discovery=MagicMock(),
            settings=self.settings,
            state=MagicMock(),
            coordinator=self.coordinator,
            observer=self.mock_observer,
        )

    def tearDown(self):
        self.db_session.close()

    def _create_mock_tg_message(self, message_id: int, chat_id: int):
        msg = MagicMock()
        msg.id = message_id
        msg.chat = MagicMock(id=chat_id)
        msg.link = f"https://t.me/c/{abs(chat_id)}/{message_id}"
        msg.document = MagicMock(file_id=f"file_id_{message_id}", file_size=100)
        msg.__str__.return_value = json.dumps(  # type: ignore
            {
                "id": message_id,
                "chat": {"id": chat_id},
                "link": msg.link,
                "document": {"file_id": f"file_id_{message_id}", "file_size": 100},
            }
        )
        return msg

    # PRUEBAS DEL RESOLVER DECLARATIVO

    def test_strategy_resolver_dispatch(self):
        """Verifica que el resolver despache la estrategia correcta según el reporte."""
        report_fulfilled = AvailabilityReport(state=AvailabilityState.FULFILLED)
        self.assertIsInstance(
            StrategyResolver.resolve(report_fulfilled), FulfilledStrategy
        )

        report_forward = AvailabilityReport(
            state=AvailabilityState.CAN_FORWARD, remotes=[MagicMock()]
        )
        self.assertIsInstance(
            StrategyResolver.resolve(report_forward), SmartForwardStrategy
        )

        report_upload = AvailabilityReport(state=AvailabilityState.NEEDS_UPLOAD)
        self.assertIsInstance(
            StrategyResolver.resolve(report_upload), CooperativeUploadStrategy
        )

    # PRUEBAS DE FULFILLED STRATEGY

    def test_fulfilled_strategy_execution(self):
        """FulfilledStrategy no transfiere bytes y marca el Job como UPLOADED."""
        source = Source.create(
            path_str="/tmp/done.txt",
            md5sum="hash_done",
            size=100,
            mtime=1.0,
            mimetype="text/plain",
            type=SourceType.FILE,
        )
        job = Job.create(
            source=source,
            chat=self.chat,
            strategy=Strategy.SINGLE,
            status=JobStatus.PENDING,
            config=StrategyConfig(
                tg_max_size=1000, user_is_premium=False, app_version="0.9.15"
            ),
        )

        strategy = FulfilledStrategy()
        result = strategy.execute(
            job, self.ctx, AvailabilityReport(state=AvailabilityState.FULFILLED)
        )

        self.assertTrue(result.is_completed)
        self.assertFalse(result.did_upload_bytes)
        self.assertEqual(result.pieces_processed, 0)
        self.assertEqual(Job.get_by_id(job.id).status, JobStatus.UPLOADED)

    # PRUEBAS DE SMART FORWARD STRATEGY

    def test_smart_forward_strategy_execution(self):
        """SmartForwardStrategy reenvía las partes vía file_id sin subir bytes."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"F" * 200)
            tmp_path = Path(tmp.name)

        try:
            source = Source.create(
                path_str=str(tmp_path),
                md5sum="hash_iso_mirror",
                size=200,
                mtime=1.0,
                mimetype="application/octet-stream",
                type=SourceType.FILE,
            )

            # Job 1 (Espejo en chat_mirror ya subido)
            job_mirror = Job.create(
                source=source,
                chat=self.chat_mirror,
                strategy=Strategy.CHUNKED,
                status=JobStatus.UPLOADED,
                config=StrategyConfig(
                    tg_max_size=100, user_is_premium=False, app_version="0.9.15"
                ),
            )
            p0_mirror = Payload.create(
                job=job_mirror,
                sequence_index=0,
                start_offset=0,
                end_offset=100,
                size=100,
                filename="file.iso.01-02",
                filename_short="h.iso.01-02",
            )
            p1_mirror = Payload.create(
                job=job_mirror,
                sequence_index=1,
                start_offset=100,
                end_offset=200,
                size=100,
                filename="file.iso.02-02",
                filename_short="h.iso.02-02",
            )

            msg0 = self._create_mock_tg_message(501, self.chat_mirror.id)
            msg1 = self._create_mock_tg_message(502, self.chat_mirror.id)
            r0 = RemotePayload.register_upload(p0_mirror, msg0, self.user)
            r1 = RemotePayload.register_upload(p1_mirror, msg1, self.user)

            # Job 2 (Nuevo destino en self.chat)
            job_target = Job.create(
                source=source,
                chat=self.chat,
                strategy=Strategy.CHUNKED,
                status=JobStatus.PENDING,
                config=StrategyConfig(
                    tg_max_size=100, user_is_premium=False, app_version="0.9.15"
                ),
            )

            # Simular respuestas de Pyrogram al reenviar
            msg_fwd0 = self._create_mock_tg_message(601, self.chat.id)
            msg_fwd1 = self._create_mock_tg_message(602, self.chat.id)
            self.mock_client.send_document.side_effect = [msg_fwd0, msg_fwd1]

            report = AvailabilityReport(
                state=AvailabilityState.CAN_FORWARD, remotes=[r0, r1]
            )

            with (
                patch("time.sleep", return_value=None),
                patch(
                    "totelegram.models.parse_message_json_data",
                    side_effect=[msg0, msg1],
                ),
            ):
                strategy = SmartForwardStrategy()
                result = strategy.execute(job_target, self.ctx, report)

            self.assertTrue(result.is_completed)
            self.assertFalse(result.did_upload_bytes)
            self.assertEqual(result.pieces_processed, 2)
            self.assertEqual(self.mock_client.send_document.call_count, 2)

            # Verificar que se crearon los nuevos RemotePayload en el chat destino
            target_remotes = list(
                RemotePayload.select().join(Payload).where(Payload.job == job_target)
            )
            self.assertEqual(len(target_remotes), 2)
            self.assertEqual(Job.get_by_id(job_target.id).status, JobStatus.UPLOADED)
            self.mock_observer.on_forward_success.assert_called_once()

        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    # PRUEBAS DE COOPERATIVE UPLOAD STRATEGY

    def test_cooperative_upload_strategy_full_flow(self):
        """Valida la subida física cooperativa pieza a pieza con notificación a eventos."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"Z" * 250)
            tmp_path = Path(tmp.name)

        try:
            source = Source.create(
                path_str=str(tmp_path),
                md5sum="hash_coop_file_250",
                size=250,
                mtime=1.0,
                mimetype="application/octet-stream",
                type=SourceType.FILE,
            )

            job = Job.create(
                source=source,
                chat=self.chat,
                strategy=Strategy.CHUNKED,
                status=JobStatus.PENDING,
                config=StrategyConfig(
                    tg_max_size=100, user_is_premium=False, app_version="0.9.15"
                ),
            )

            # Simular 3 mensajes subidos devueltos por Pyrogram
            msg_up0 = self._create_mock_tg_message(701, self.chat.id)
            msg_up1 = self._create_mock_tg_message(702, self.chat.id)
            msg_up2 = self._create_mock_tg_message(703, self.chat.id)
            self.mock_client.send_document.side_effect = [msg_up0, msg_up1, msg_up2]

            report = AvailabilityReport(state=AvailabilityState.NEEDS_UPLOAD)
            strategy = CooperativeUploadStrategy()

            with patch("time.sleep", return_value=None):
                result = strategy.execute(job, self.ctx, report)

            self.assertTrue(result.is_completed)
            self.assertTrue(result.did_upload_bytes)
            self.assertEqual(result.pieces_processed, 3)
            self.assertEqual(self.mock_client.send_document.call_count, 3)

            # Verificar que todos los payloads quedaron con MD5 y RemotePayload registrado
            self.assertEqual(Payload.total_pending_for_job(job), 0)
            self.assertEqual(Job.get_by_id(job.id).status, JobStatus.UPLOADED)

            # Verificar hooks del observador
            self.assertEqual(self.mock_observer.on_payload_start.call_count, 3)
            self.assertEqual(self.mock_observer.on_payload_complete.call_count, 3)

        finally:
            if tmp_path.exists():
                tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
