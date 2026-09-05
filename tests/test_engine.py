import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import MagicMock, patch

from tartape.exceptions import PathConstraintReportError

from totelegram.concurrency import ConcurrencyCoordinator
from totelegram.database import DatabaseSession
from totelegram.engine.events import ProgressMultiplexer, UploadObserver
from totelegram.engine.factory import PayloadStreamFactory, UploadStream
from totelegram.engine.pipeline import JobPipeline
from totelegram.engine.planner import JobPlanner, PathLengthExceededError
from totelegram.models import (
    Job,
    Payload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import (
    AvailabilityState,
    JobStatus,
    Strategy,
)
from totelegram.stream import FileVolume
from totelegram.types import AvailabilityReport, StrategyResult, UploadContext


class TestEnginePipelineAndComponents(unittest.TestCase):
    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.mock_client = MagicMock()
        self.mock_observer = MagicMock(spec=UploadObserver)
        self.coordinator = ConcurrencyCoordinator(self.db, node_id="test_pipeline_node")

        self.chat = TelegramChat.create(
            id=-100123456, title="Canal Pipeline", type="channel"
        )
        self.user = TelegramUser.create(
            id=12345, first_name="PipelineUser", is_premium=False
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

    # --- PRUEBAS DE FACTORY & MULTIPLEXER ---

    def test_payload_stream_factory_naming_resolution(self):
        """Si el nombre <= 55 caracteres va limpio; si es > 55 usa nombre corto y pasa el largo a caption."""
        source = Source.create(
            path_str="/tmp/test.txt",
            md5sum="hash1",
            size=100,
            mtime=1.0,
            mimetype="text/plain",
        )
        job = Job.formalize_intent(source, self.chat, False, 1000)

        # Caso corto
        p_short = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=100,
            size=100,
            filename="foto_vacaciones.jpg",
            filename_short="h123.jpg",
        )
        name, caption = PayloadStreamFactory.resolve_naming(
            p_short, max_filename_len=55
        )
        self.assertEqual(name, "foto_vacaciones.jpg")
        self.assertEqual(caption, "")

        # Caso largo (> 55 caracteres)
        long_name = "a" * 60 + ".jpg"
        p_long = Payload.create(
            job=job,
            sequence_index=1,
            start_offset=0,
            end_offset=100,
            size=100,
            filename=long_name,
            filename_short="short_hash.jpg",
        )
        name_res, caption_res = PayloadStreamFactory.resolve_naming(
            p_long, max_filename_len=55
        )
        self.assertEqual(name_res, "short_hash.jpg")
        self.assertEqual(caption_res, long_name)

    def test_payload_stream_factory_creates_file_volume(self):
        """Para SourceType.FILE, create_stream debe retornar un UploadStream conteniendo FileVolume."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"data" * 25)
            tmp_path = Path(tmp.name)

        try:
            source = Source.get_or_create_from_filepath(tmp_path)
            job = Job.formalize_intent(source, self.chat, False, 1000)
            job.prepare_chunks(tmp_path, self.settings)
            payload = job.payloads.first()

            upload_stream = PayloadStreamFactory.create_stream(payload, tmp_path)
            self.assertIsInstance(upload_stream, UploadStream)
            self.assertIsInstance(upload_stream.stream, FileVolume)
            self.assertEqual(upload_stream.size, 100)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_progress_multiplexer_dispatch(self):
        """Verifica que el multiplexor notifique al heartbeat (DB) y al observer (UI) en cada chunk."""
        mock_heartbeat = MagicMock()
        mock_observer = MagicMock()

        multiplexer = ProgressMultiplexer(
            heartbeat=mock_heartbeat, observer=mock_observer
        )
        multiplexer(current=500, total=1000)

        mock_heartbeat.pulse.assert_called_once_with(500, 1000)
        mock_observer.on_payload_progress.assert_called_once_with(500, 1000)

    # --- PRUEBAS DE JOB PLANNER ---

    def test_job_planner_creates_new_job_and_chunks(self):
        """JobPlanner.plan debe crear el Source, formalizar el Job y preparar los Payloads en DB."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"X" * 2500)
            tmp_path = Path(tmp.name)

        try:
            job = JobPlanner.plan(tmp_path, self.ctx)

            self.assertIsNotNone(job)
            self.assertEqual(job.status, JobStatus.PENDING)
            self.assertEqual(job.strategy, Strategy.CHUNKED)
            # 2500 bytes con límite de 1000 = 3 partes (1000, 1000, 500)
            self.assertEqual(job.payloads.count(), 3)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_job_planner_force_resets_previous_job(self):
        """Con force=True, JobPlanner debe invalidar el Job previo (mark_deleted) y crear uno nuevo."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"M" * 500)
            tmp_path = Path(tmp.name)

        try:
            job_1 = JobPlanner.plan(tmp_path, self.ctx)
            job_1_id = job_1.id

            # Planificar de nuevo con force=True
            with patch("totelegram.engine.planner.delete_snapshot") as mock_del_snap:
                job_2 = JobPlanner.plan(tmp_path, self.ctx, force=True)

            mock_del_snap.assert_called_once_with(tmp_path)
            self.assertNotEqual(job_1_id, job_2.id)

            old_job = Job.get_by_id(job_1_id)
            self.assertEqual(old_job.status, JobStatus.DELETED)
            self.assertTrue(old_job.deleted_at > 0)
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

    # --- PRUEBAS DEL PIPELINE COMPLETO (E2E) ---

    def test_job_pipeline_e2e_successful_execution(self):
        """Ejecución completa del pipeline orquestando Planificación -> Discovery -> Estrategia -> Snapshot."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"CONTENIDO" * 50)
            tmp_path = Path(tmp.name)

        try:
            mock_strategy = MagicMock()
            mock_strategy.execute.return_value = StrategyResult(
                strategy_name="MockStrategy",
                pieces_processed=1,
                did_upload_bytes=True,
                is_completed=True,
                message="Subida OK",
            )

            self.ctx.discovery.investigate.return_value = AvailabilityReport(  # type: ignore
                state=AvailabilityState.NEEDS_UPLOAD
            )

            pipeline = JobPipeline(self.ctx)

            with (
                patch(
                    "totelegram.engine.pipeline.StrategyResolver.resolve",
                    return_value=mock_strategy,
                ),
                patch.object(SnapshotService, "generate_snapshot") as mock_snapshot,
                patch.object(Payload, "total_pending_for_job", return_value=0),
            ):
                result = pipeline.process(tmp_path, is_last_in_batch=True)

            self.assertTrue(result.is_completed)
            self.assertTrue(result.snapshot_generated)
            self.assertEqual(Job.get_by_id(result.job.id).status, JobStatus.UPLOADED)

            # Notificaciones recibidas por el Observer
            self.mock_observer.on_job_start.assert_called_once()
            self.mock_observer.on_job_complete.assert_called_once_with(result.job, True)
            mock_snapshot.assert_called_once_with(result.job)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_job_pipeline_applies_inter_job_pause(self):
        """Valida que si no es el último archivo y se subieron bytes, el pipeline ejecute la pausa entre Jobs."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"12345")
            tmp_path = Path(tmp.name)

        try:
            self.settings.upload_pause_range = [1, 1]  # Pausa forzada de 1 minuto (60s)
            self.ctx.discovery.investigate.return_value = AvailabilityReport(  # type: ignore
                state=AvailabilityState.NEEDS_UPLOAD
            )

            mock_strategy = MagicMock()
            mock_strategy.execute.return_value = StrategyResult(
                strategy_name="MockStrategy",
                pieces_processed=1,
                did_upload_bytes=True,
                is_completed=True,
            )

            pipeline = JobPipeline(self.ctx)

            with (
                patch(
                    "totelegram.engine.pipeline.StrategyResolver.resolve",
                    return_value=mock_strategy,
                ),
                patch.object(SnapshotService, "generate_snapshot"),
                patch.object(Payload, "total_pending_for_job", return_value=0),
                patch("time.sleep", return_value=None),  # Evitar demoras en el test
            ):
                pipeline.process(tmp_path, is_last_in_batch=False)

            self.mock_observer.on_pause_start.assert_called_once_with(60, "Inter-job")
            self.assertEqual(self.mock_observer.on_pause_tick.call_count, 60)
            self.mock_observer.on_pause_end.assert_called_once()
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
