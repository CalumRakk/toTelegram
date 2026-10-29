import json
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, patch

from totelegram.concurrency import ConcurrencyCoordinator
from totelegram.database import DatabaseSession
from totelegram.engine.events import UploadObserver
from totelegram.engine.pipeline import JobPipeline
from totelegram.engine.uploader import CooperativeUploader
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.publisher import SnapshotPublisher
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import ExecutionDecision
from totelegram.types import (
    IncompatibleCapacityError,
    InvestigationReport,
    PermissionDeniedCleanupError,
    StrategyResult,
    UploadContext,
)


class TestPipelineDecisionTree(unittest.TestCase):
    """Batería de pruebas de integración para la máquina de estados y el árbol de decisiones del pipeline."""

    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.mock_client = MagicMock()
        self.mock_observer = MagicMock(spec=UploadObserver)
        self.coordinator = ConcurrencyCoordinator(
            self.db, node_id="test_node_decision_tree", client=self.mock_client
        )

        self.chat = TelegramChat.create(
            id=-100999888777, title="Canal Máquina de Estados", type="channel"
        )
        self.user = TelegramUser.create(
            id=123456, first_name="TreeTester", is_premium=False
        )

        self.settings = MagicMock()
        self.settings.chat_id = self.chat.id
        self.settings.telegram_account_id = self.user.id
        self.settings.upload_limit_rate_kbps = 0
        self.settings.max_filename_length = 55
        self.settings.upload_pause_range = [0, 0]
        self.settings.exclude_files = []
        self.settings.tg_max_size_normal = 2000
        self.settings.tg_max_size_premium = 4000
        self.settings.auto_truncate = False

        # Sesión limpia
        self.ctx = UploadContext(
            tg_chat=MagicMock(id=self.chat.id, title=self.chat.title),
            owner=self.user,
            client=self.mock_client,
            db=self.db,
            discovery=MagicMock(),
            settings=self.settings,
            coordinator=self.coordinator,
        )

        self.patcher_lock = patch("totelegram.concurrency.TelegramAccountLock")
        self.mock_account_lock = self.patcher_lock.start()

    def tearDown(self):
        self.patcher_lock.stop()
        self.db_session.close()

    def _create_temp_file(self, content: bytes = b"DUMMY_DATA_PIPELINE") -> Path:
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp.close()
        return Path(tmp.name)

    def _create_mock_message(self, message_id: int):
        msg = MagicMock()
        msg.id = message_id
        msg.chat = MagicMock(id=self.chat.id)
        msg.empty = False
        msg.document = MagicMock(file_size=1000, file_id=f"file_id_{message_id}")
        msg.__str__.return_value = json.dumps(
            {"id": message_id, "chat": {"id": self.chat.id}}
        )
        return msg

    def _simulate_strategy_success(self, job: Job, file_path: Path):
        """Simula la ejecución de subida registrando los trozos faltantes en la base de datos."""
        payloads = job.prepare_chunks(file_path, self.settings)
        for p in payloads:
            if not p.has_remote:
                msg = self._create_mock_message(900 + p.sequence_index)
                RemotePayload.register_upload(p, msg, self.user)
        return StrategyResult(
            strategy_name="MockCooperativeUploadStrategy",
            pieces_processed=len(payloads),
            did_upload_bytes=True,
            is_completed=True,
        )

    # ALREADY_FULFILLED

    def test_01_already_fulfilled_stops_immediately_with_zero_byte_transfer(self):
        """Si el archivo ya existe al 100% en el canal (Snapshot verificado), se detiene sin subir nada."""
        file_path = self._create_temp_file()
        try:
            self.ctx.discovery.investigate.return_value = InvestigationReport(
                decision=ExecutionDecision.ALREADY_FULFILLED,
                detail="El archivo ya está 100% en este chat.",
            )

            pipeline = JobPipeline(session=self.ctx, observer=self.mock_observer)

            with patch.object(CooperativeUploader, "upload") as mock_exec:
                result = pipeline.process(file_path, force=False)

            self.assertTrue(result.is_completed)
            self.assertFalse(result.snapshot_generated)
            self.assertFalse(result.strategy_result.did_upload_bytes)
            self.assertEqual(result.strategy_result.pieces_processed, 0)

            # La estrategia de subida física no debe invocarse
            mock_exec.assert_not_called()
            self.mock_observer.on_job_complete.assert_called_once_with(
                result.job, snapshot_created=False
            )
        finally:
            file_path.unlink(missing_ok=True)

    # PERMISSION_DENIED_CLEANUP

    def test_02_permission_denied_cleanup_raises_fail_fast_error(self):
        """Si se requiere limpiar o reubicar trozos pero no hay permisos de eliminación, lanza PermissionDeniedCleanupError."""
        file_path = self._create_temp_file()
        try:
            self.ctx.discovery.investigate.return_value = InvestigationReport(
                decision=ExecutionDecision.PERMISSION_DENIED_CLEANUP,
                detail="Faltan permisos para eliminar mensajes antiguos.",
            )

            pipeline = JobPipeline(session=self.ctx, observer=self.mock_observer)

            with self.assertRaises(PermissionDeniedCleanupError) as ctx_err:
                pipeline.process(file_path, force=True)

            self.assertEqual(ctx_err.exception.chat_id, self.chat.id)
            self.mock_client.send_document.assert_not_called()
        finally:
            file_path.unlink(missing_ok=True)

    # INCOMPATIBLE_CAPACITY

    def test_03_incompatible_capacity_raises_error_without_force(self):
        """Si el corte previo supera la capacidad de la cuenta activa sin --force, bloquea con IncompatibleCapacityError."""
        file_path = self._create_temp_file()
        try:
            self.ctx.discovery.investigate.return_value = InvestigationReport(
                decision=ExecutionDecision.INCOMPATIBLE_CAPACITY,
                detail="El corte previo de 4 GB supera el límite de 2 GB de la cuenta estándar.",
            )

            pipeline = JobPipeline(session=self.ctx, observer=self.mock_observer)

            with self.assertRaises(IncompatibleCapacityError) as ctx_err:
                pipeline.process(file_path, force=False)

            self.assertEqual(ctx_err.exception.job_chunk_size, 2000)
            self.assertFalse(ctx_err.exception.is_premium)
        finally:
            file_path.unlink(missing_ok=True)

    # SEAL_HEALED

    def test_04_seal_healed_publishes_snapshot_without_uploading_file_bytes(
        self,
    ):
        """Si todas las partes físicas están en Telegram (N/N) pero falta el snapshot, lo sella sin transferir datos."""
        file_path = self._create_temp_file()
        try:
            source = Source.get_or_create_from_filepath(file_path)
            job = Job.formalize_intent(source, self.chat, chunk_size=2000)
            job.prepare_chunks(file_path, self.settings)
            payload = job.payloads.first()

            msg = self._create_mock_message(801)
            RemotePayload.register_upload(payload, msg, self.user)

            self.ctx.discovery.investigate.return_value = InvestigationReport(
                decision=ExecutionDecision.SEAL_HEALED,
                valid_remotes=list(job.payloads.first().remotes),
                total_parts=1,
            )

            pipeline = JobPipeline(session=self.ctx, observer=self.mock_observer)
            mock_published_msg = MagicMock(id=9999)

            with (
                patch.object(CooperativeUploader, "upload") as mock_exec,
                patch.object(
                    SnapshotService,
                    "generate_snapshot",
                    return_value=(MagicMock(), file_path),
                ) as mock_snap,
                patch.object(
                    SnapshotPublisher, "publish", return_value=mock_published_msg
                ) as mock_pub,
            ):
                result = pipeline.process(file_path, force=False)

            self.assertTrue(result.is_completed)
            self.assertTrue(result.snapshot_generated)
            self.assertFalse(result.strategy_result.did_upload_bytes)
            self.assertEqual(result.strategy_result.strategy_name, "SealHealing")

            mock_exec.assert_not_called()
            mock_snap.assert_called_once()
            mock_pub.assert_called_once()

            refreshed_job = Job.get_by_id(job.id)
            self.assertTrue(refreshed_job.has_remote_snapshot)
            self.assertEqual(refreshed_job.snapshot_message_id, 9999)
        finally:
            file_path.unlink(missing_ok=True)

    # RELOCATED_AND_RESUMED

    def test_05_relocated_and_resumed_copies_to_bottom_and_continues(self):
        """Con --force y corte compatible, reubica partes existentes al fondo y sube las pendientes."""
        file_path = self._create_temp_file()
        try:
            source = Source.get_or_create_from_filepath(file_path)
            job = Job.formalize_intent(source, self.chat, chunk_size=2000)
            job.prepare_chunks(file_path, self.settings)
            payload = job.payloads.first()

            msg_old = self._create_mock_message(101)
            remote = RemotePayload.register_upload(payload, msg_old, self.user)

            self.ctx.discovery.investigate.return_value = InvestigationReport(
                decision=ExecutionDecision.RELOCATED_AND_RESUMED,
                valid_remotes=[remote],
                total_parts=2,
            )

            pipeline = JobPipeline(session=self.ctx, observer=self.mock_observer)

            with (
                patch(
                    "totelegram.engine.pipeline.ChunkRelocator.relocate"
                ) as mock_relocate,
                patch.object(
                    CooperativeUploader,
                    "upload",
                    side_effect=lambda j, *args, **kwargs: (
                        self._simulate_strategy_success(j, file_path)
                    ),
                ) as mock_exec,
                patch.object(
                    SnapshotService,
                    "generate_snapshot",
                    return_value=(MagicMock(), file_path),
                ),
                patch.object(
                    SnapshotPublisher,
                    "publish",
                    return_value=MagicMock(id=5555),
                ),
            ):
                result = pipeline.process(file_path, force=True)

            self.assertTrue(result.is_completed)
            mock_relocate.assert_called_once_with(
                client=self.ctx.client,
                db=self.ctx.db,
                chat_id=self.chat.id,
                remotes=[remote],
            )
            mock_exec.assert_called_once()
        finally:
            file_path.unlink(missing_ok=True)

    # CLEAN_RESTART

    def test_06_clean_restart_purges_previous_incompatible_job_and_restarts(
        self,
    ):
        """Con --force ante un corte incompatible, purga trozos viejos en Telegram y BD y reinicia desde cero."""
        file_path = self._create_temp_file()
        try:
            source = Source.get_or_create_from_filepath(file_path)
            # Job viejo con bloques de 4000
            job_old = Job.formalize_intent(source, self.chat, chunk_size=4000)
            p_old = Payload.create(
                job=job_old,
                sequence_index=0,
                start_offset=0,
                end_offset=4000,
                size=4000,
                filename="f.01",
                filename_short="f.01",
            )
            msg_old = self._create_mock_message(201)
            remote_old = RemotePayload.register_upload(p_old, msg_old, self.user)

            self.ctx.discovery.investigate.return_value = InvestigationReport(
                decision=ExecutionDecision.CLEAN_RESTART,
                valid_remotes=[remote_old],
                total_parts=1,
            )

            pipeline = JobPipeline(session=self.ctx, observer=self.mock_observer)

            with (
                patch.object(
                    CooperativeUploader,
                    "upload",
                    side_effect=lambda j, *args, **kwargs: (
                        self._simulate_strategy_success(j, file_path)
                    ),
                ),
                patch.object(
                    SnapshotService,
                    "generate_snapshot",
                    return_value=(MagicMock(), file_path),
                ),
                patch.object(
                    SnapshotPublisher,
                    "publish",
                    return_value=MagicMock(id=6666),
                ),
            ):
                result = pipeline.process(file_path, force=True)

            self.assertTrue(result.is_completed)
            # Verificamos que se ordenó borrar el mensaje incompatible de Telegram
            self.mock_client.delete_messages.assert_called_with(
                chat_id=self.chat.id, message_ids=[201]
            )
            # Verificamos que el Job resultante se ajustó al tamaño de corte de la cuenta activa (2000)
            self.assertEqual(result.job.chunk_size, 2000)
        finally:
            file_path.unlink(missing_ok=True)

    # RESUMED_IN_PLACE

    def test_07_resumed_in_place_continues_without_force_and_without_moving_chunks(
        self,
    ):
        """Sin --force y corte compatible, continúa subiendo las piezas faltantes en su sitio sin reubicar."""
        file_path = self._create_temp_file()
        try:
            self.ctx.discovery.investigate.return_value = InvestigationReport(
                decision=ExecutionDecision.RESUMED_IN_PLACE,
                total_parts=3,
            )

            pipeline = JobPipeline(session=self.ctx, observer=self.mock_observer)

            with (
                patch(
                    "totelegram.engine.pipeline.ChunkRelocator.relocate"
                ) as mock_relocate,
                patch.object(
                    CooperativeUploader,
                    "upload",
                    side_effect=lambda j, *args, **kwargs: (
                        self._simulate_strategy_success(j, file_path)
                    ),
                ) as mock_exec,
                patch.object(
                    SnapshotService,
                    "generate_snapshot",
                    return_value=(MagicMock(), file_path),
                ),
                patch.object(
                    SnapshotPublisher,
                    "publish",
                    return_value=MagicMock(id=7777),
                ),
            ):
                result = pipeline.process(file_path, force=False)

            self.assertTrue(result.is_completed)
            # No se debe llamar al reubicador
            mock_relocate.assert_not_called()
            mock_exec.assert_called_once()
        finally:
            file_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
