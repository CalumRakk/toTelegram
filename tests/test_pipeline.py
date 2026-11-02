import json
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import MagicMock, patch

from tartape.exceptions import PathConstraintReportError

from totelegram.common.helpers import get_utc_now
from totelegram.concurrency import ConcurrencyCoordinator
from totelegram.database import DatabaseSession
from totelegram.engine.events import UploadObserver
from totelegram.engine.pipeline import JobPipeline
from totelegram.engine.planner import JobPlanner, PathLengthExceededError
from totelegram.engine.uploader import CooperativeUploader
from totelegram.exceptions import (
    ConcurrencyError,
    IncompatibleCapacityError,
    PermissionDeniedCleanupError,
)
from totelegram.models import (
    Claim,
    Job,
    Payload,
    RemotePayload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.publisher import SnapshotPublisher
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import ExecutionDecision, ResourceType
from totelegram.types import (
    InvestigationReport,
    StrategyResult,
    UploadContext,
)


class TestJobPlanner(unittest.TestCase):
    """Pruebas unitarias para la planificación de intenciones de Jobs."""

    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.chat = TelegramChat.create(
            id=-100123456, title="Canal Planner", type="channel"
        )
        self.user = TelegramUser.create(
            id=12345, first_name="PlannerUser", is_premium=False
        )

        self.settings = MagicMock()
        self.settings.chat_id = self.chat.id
        self.settings.telegram_account_id = self.user.id
        self.settings.upload_limit_rate_kbps = 0
        self.settings.max_filename_length = 55
        self.settings.upload_pause_range = [0, 0]
        self.settings.exclude_files = []
        self.settings.tg_max_size_normal = 1000
        self.settings.tg_max_size_premium = 2000

        self.ctx = UploadContext(
            tg_chat=MagicMock(id=self.chat.id, title=self.chat.title),
            owner=self.user,
            client=MagicMock(),
            db=self.db,
            discovery=MagicMock(),
            settings=self.settings,
            coordinator=MagicMock(),
        )

    def tearDown(self):
        self.db_session.close()

    def test_job_planner_creates_new_job_and_chunks(self):
        """JobPlanner.plan debe formalizar el Job y preparar los Payloads bajo demanda."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"X" * 2500)
            tmp_path = Path(tmp.name)

        try:
            job = JobPlanner.plan(tmp_path, self.ctx)

            self.assertIsNotNone(job)
            self.assertFalse(job.is_completed_local)
            self.assertTrue(job.is_chunked)
            self.assertEqual(job.chunk_size, 1000)

            payloads = job.prepare_chunks(tmp_path, self.settings)
            self.assertEqual(len(payloads), 3)
            self.assertEqual(job.payloads.count(), 3)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_job_planner_catches_tartape_path_length_exceeded(self):
        """Si tartape lanza PathConstraintReportError, JobPlanner debe elevar PathLengthExceededError."""
        with TemporaryDirectory() as tmp_dir:
            folder_path = Path(tmp_dir) / "carpeta_problematica"
            folder_path.mkdir()

            with patch(
                "tartape.create", side_effect=PathConstraintReportError("Ruta larga")
            ):
                with self.assertRaises(PathLengthExceededError) as ctx:
                    JobPlanner.plan(folder_path, self.ctx)

                self.assertEqual(ctx.exception.path, folder_path)


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
            self.mock_client.delete_messages.assert_called_with(
                chat_id=self.chat.id, message_ids=[201]
            )
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
            mock_relocate.assert_not_called()
            mock_exec.assert_called_once()
        finally:
            file_path.unlink(missing_ok=True)

    # SALVAGUARDA DE CONCURRENCIA ANTE --force

    def test_08_force_cannot_purge_job_if_another_node_holds_active_claim(self):
        """Incluso con --force, un nodo no puede purgar el Job si otro nodo está subiendo activamente una pieza."""
        file_path = self._create_temp_file()
        try:
            source = Source.get_or_create_from_filepath(file_path)
            job = Job.formalize_intent(source, self.chat, chunk_size=2000)
            payload = Payload.create(
                job=job,
                sequence_index=0,
                start_offset=0,
                end_offset=2000,
                size=2000,
                filename="f.01",
                filename_short="f.01",
            )

            Claim.create(
                resource_id=f"payload:{payload.id}",
                resource_type=ResourceType.PAYLOAD,
                node_id="node_rival",
                expires_at=get_utc_now() + timedelta(seconds=120),
            )

            pipeline = JobPipeline(session=self.ctx, observer=self.mock_observer)

            with self.assertRaises(ConcurrencyError) as ctx:
                pipeline._ensure_no_active_claims_on_job(job)

            self.assertIn("node_rival", str(ctx.exception))
        finally:
            file_path.unlink(missing_ok=True)

    # PAUSAS INTER-JOB Y DUPLICADOS

    def test_09_job_pipeline_applies_inter_job_pause(self):
        """Verifica que el pipeline respete el rango de pausa entre archivos si no es el último."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"12345")
            tmp_path = Path(tmp.name)

        try:
            self.settings.upload_pause_range = [1, 1]
            self.ctx.discovery.investigate.return_value = InvestigationReport(
                decision=ExecutionDecision.CLEAN_RESTART
            )

            pipeline = JobPipeline(session=self.ctx, observer=self.mock_observer)

            with (
                patch.object(
                    CooperativeUploader,
                    "upload",
                    return_value=StrategyResult(
                        strategy_name="MockUploader",
                        pieces_processed=1,
                        did_upload_bytes=True,
                        is_completed=True,
                    ),
                ),
                patch.object(
                    SnapshotService,
                    "generate_snapshot",
                    return_value=(MagicMock(), tmp_path),
                ),
                patch(
                    "totelegram.packaging.publisher.SnapshotPublisher.publish",
                    return_value=MagicMock(id=111),
                ),
                patch.object(Payload, "total_pending_for_job", return_value=0),
                patch("time.sleep", return_value=None),
            ):
                pipeline.process(tmp_path, is_last_in_batch=False)

            self.mock_observer.on_pause_start.assert_called_once_with(60, "Inter-job")
            self.assertEqual(self.mock_observer.on_pause_tick.call_count, 60)
            self.mock_observer.on_pause_end.assert_called_once()
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_10_job_pipeline_notifies_observer_on_potential_duplicates(self):
        """Verifica que si discovery detecta duplicados potenciales, el pipeline notifique al observer."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"CONTENIDO" * 50)
            tmp_path = Path(tmp.name)

        try:
            duplicate_candidate = MagicMock()
            self.ctx.discovery.investigate.return_value = InvestigationReport(
                decision=ExecutionDecision.CLEAN_RESTART,
                potential_duplicates=[duplicate_candidate],
            )

            pipeline = JobPipeline(session=self.ctx, observer=self.mock_observer)

            with (
                patch.object(
                    CooperativeUploader,
                    "upload",
                    return_value=StrategyResult(
                        strategy_name="MockUploader",
                        pieces_processed=1,
                        did_upload_bytes=True,
                        is_completed=True,
                    ),
                ),
                patch(
                    "totelegram.packaging.snapshot.SnapshotService.generate_snapshot",
                    return_value=(MagicMock(), tmp_path),
                ),
                patch(
                    "totelegram.packaging.publisher.SnapshotPublisher.publish",
                    return_value=MagicMock(id=1),
                ),
                patch.object(Payload, "total_pending_for_job", return_value=0),
            ):
                pipeline.process(tmp_path, is_last_in_batch=True)

            self.mock_observer.on_potential_duplicates.assert_called_once_with(
                [duplicate_candidate]
            )
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
