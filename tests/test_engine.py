import json
import tempfile
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import ANY, MagicMock, patch

import tartape
from tartape.exceptions import PathConstraintReportError

from totelegram.concurrency import ConcurrencyCoordinator
from totelegram.database import DatabaseSession
from totelegram.engine.downloader import DownloadEngine
from totelegram.engine.events import ProgressMultiplexer, UploadObserver
from totelegram.engine.factory import PayloadStreamFactory, UploadStream
from totelegram.engine.pipeline import JobPipeline
from totelegram.engine.planner import JobPlanner, PathLengthExceededError
from totelegram.engine.uploader import CooperativeUploader
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import (
    ExecutionDecision,
)
from totelegram.stream import FileVolume
from totelegram.types import InvestigationReport, StrategyResult, UploadContext


class TestEnginePipelineAndComponents(unittest.TestCase):
    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.mock_client = MagicMock()
        self.mock_observer = MagicMock(spec=UploadObserver)
        self.coordinator = ConcurrencyCoordinator(
            self.db, node_id="test_pipeline_node", client=self.mock_client
        )

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

        # UploadSession limpia sin dependencias de CLI ni observer
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
        job = Job.formalize_intent(source, self.chat, 1000)

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
        self.assertIn("#totelegram_chunk", caption)
        self.assertIn("#src_hash1", caption)

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
        self.assertIn("#totelegram_chunk", caption_res)
        self.assertIn("#src_hash1", caption_res)
        self.assertIn("a" * 30, caption_res)

    def test_payload_stream_factory_creates_file_volume(self):
        """Para SourceType.FILE, create_stream debe retornar un UploadStream conteniendo FileVolume."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"data" * 25)
            tmp_path = Path(tmp.name)

        try:
            source = Source.get_or_create_from_filepath(tmp_path)
            job = Job.formalize_intent(source, self.chat, 1000)
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

            # Las piezas se generan de forma perezosa al llamar a prepare_chunks (o en la estrategia)
            payloads = job.prepare_chunks(tmp_path, self.settings)
            self.assertEqual(len(payloads), 3)
            self.assertEqual(job.payloads.count(), 3)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_job_planner_force_resets_previous_job(self):
        """Con force=True, JobPlanner debe purgar físicamente el Job previo y crear uno nuevo."""
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"M" * 500)
            tmp_path = Path(tmp.name)

        try:
            job_1 = JobPlanner.plan(tmp_path, self.ctx)
            # Preparamos chunks para simular que job_1 tenía datos
            job_1.prepare_chunks(tmp_path, self.settings)
            self.assertEqual(job_1.payloads.count(), 1)

            # Planificar de nuevo con force=True
            with patch("totelegram.engine.planner.delete_snapshot") as mock_del_snap:
                job_2 = JobPlanner.plan(tmp_path, self.ctx, force=True)

            mock_del_snap.assert_called_once_with(tmp_path)

            # En la nueva arquitectura, JobPlanner preserva el Job para que Discovery decida
            self.assertEqual(Job.select().count(), 1)
            self.assertEqual(job_2.id, job_1.id)
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
        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"CONTENIDO" * 50)
            tmp_path = Path(tmp.name)

        try:

            def fake_execute(job, *args, **kwargs):
                job.prepare_chunks(tmp_path, self.settings)
                return StrategyResult(
                    strategy_name="MockUploader",
                    pieces_processed=1,
                    did_upload_bytes=True,
                    is_completed=True,
                    message="Subida OK",
                )

            self.ctx.discovery.investigate.return_value = InvestigationReport(
                decision=ExecutionDecision.CLEAN_RESTART
            )

            # Inyectamos el observer al instanciar JobPipeline
            pipeline = JobPipeline(session=self.ctx, observer=self.mock_observer)
            mock_sent_msg = MagicMock(id=98765)
            with (
                patch.object(CooperativeUploader, "upload", side_effect=fake_execute),
                patch.object(
                    SnapshotService,
                    "generate_snapshot",
                    return_value=(MagicMock(), tmp_path),
                ) as mock_snapshot,
                patch(
                    "totelegram.engine.pipeline.SnapshotPublisher.publish",
                    return_value=mock_sent_msg,
                ) as mock_publish,
                patch.object(Payload, "total_pending_for_job", return_value=0),
            ):
                result = pipeline.process(tmp_path, is_last_in_batch=True)

                self.assertTrue(result.is_completed)
                self.assertTrue(result.snapshot_generated)
                self.assertTrue(Job.get_by_id(result.job.id).is_completed_local)
                self.assertEqual(
                    Job.get_by_id(result.job.id).snapshot_message_id, 98765
                )

                self.mock_observer.on_job_start.assert_called_once()
                self.mock_observer.on_job_complete.assert_called_once_with(
                    result.job, True
                )
                mock_snapshot.assert_called_once_with(result.job, staging_dir=ANY)
                mock_publish.assert_called_once()
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_job_pipeline_applies_inter_job_pause(self):
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

    def test_job_pipeline_notifies_observer_on_potential_duplicates(self):
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

    def test_downloader_restores_empty_zero_byte_file_without_error(self):
        """Valida que un archivo de 0 bytes dentro de una cinta TAR se restaure
        directamente sin fallar por falta de fragmentos (cortocircuito)."""

        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            source_dir = base_dir / "proyecto"
            source_dir.mkdir()

            # Archivo normal y archivo vacío de 0 bytes
            (source_dir / "app.py").write_text("print('hello')")
            (source_dir / "__init__.py").write_bytes(b"")  # 0 bytes

            # Crear tape y snapshot
            tape = tartape.record(source_dir, checksum="md5", overwrite=True)
            source = Source.create_from_tape(tape, exclusion_patterns=[])
            job = Job.formalize_intent(source, self.chat, 10000)
            job.prepare_chunks(source_dir, self.settings)

            # Mock de subida de la parte 1
            payload = job.payloads.first()
            mock_msg = MagicMock(id=881, chat=MagicMock(id=self.chat.id), empty=False)
            mock_msg.__str__.return_value = json.dumps(
                {
                    "id": 881,
                    "chat": {"id": self.chat.id, "type": "channel"},
                    "link": "https://t.me/c/123456/881",
                }
            )
            RemotePayload.register_upload(payload, mock_msg, self.user)

            manifest, snapshot_path = SnapshotService.generate_snapshot(job)

            # Restaurar quirúrgicamente el archivo vacío de 0 bytes
            restore_dir = base_dir / "restored"
            engine = DownloadEngine(client=self.mock_client)

            report = engine.restore_single_file(
                manifest=manifest,
                relative_path="proyecto/__init__.py",
                output_dir=restore_dir,
                snapshot_path=snapshot_path,
                force=True,
            )

            # Debe haber tenido éxito sin descargar piezas
            self.assertTrue(report.is_success)
            self.assertEqual(report.total_bytes_downloaded, 0)

            restored_file = restore_dir / "proyecto/__init__.py"
            self.assertTrue(restored_file.exists())
            self.assertEqual(restored_file.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
