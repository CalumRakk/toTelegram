import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from totelegram.cli.commands.backup import backup_folders
from totelegram.cli.commands.send import send_files
from totelegram.cli.logic import (
    CandidateItem,
    UploadRequest,
    run_upload_flow,
)
from totelegram.cli.state import CLIState
from totelegram.database import DatabaseSession
from totelegram.identity import SettingsManager
from totelegram.types import JobExecutionResult, StrategyResult

runner = CliRunner()


class TestCliUploadFlow(unittest.TestCase):
    """Pruebas del runner unificado de subidas y los objetos de orquestación."""

    def setUp(self):
        # Base de datos SQLite en memoria para que db_proxy esté inicializado
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.tmp_file = NamedTemporaryFile(delete=False)  # noqa: SIM115
        self.tmp_file.write(b"DATA_UPLOAD_TEST" * 50)
        self.tmp_file.close()
        self.file_path = Path(self.tmp_file.name)

    def tearDown(self):
        if self.file_path.exists():
            self.file_path.unlink()
        self.db_session.close()

    def _build_mock_cli_state(self) -> MagicMock:
        """Helper para construir un mock consistente de CLIState."""
        mock_state = MagicMock(spec=CLIState)
        mock_state.profile_name = "test_profile"
        mock_state.manager = MagicMock(spec=SettingsManager)
        mock_state.manager.resolve_profile_name.return_value = "test_profile"
        mock_state.manager.database_path = ":memory:"

        @contextmanager
        def fake_scope():
            yield MagicMock(), self.db

        mock_state.scope = fake_scope

        mock_settings = MagicMock()
        mock_settings.has_custom_app = True
        mock_settings.auto_truncate = False
        mock_settings.exclude_files = []
        mock_settings.max_filesize_bytes = 10**9
        mock_state.manager.get_settings.return_value = mock_settings
        mock_state.settings = mock_settings
        return mock_state

    def _build_mock_session(self, mock_state: MagicMock) -> MagicMock:
        """Helper para construir un mock de UploadSession con settings tipados."""
        mock_ctx = MagicMock()
        mock_ctx.db = self.db
        mock_ctx.owner.first_name = "TestOwner"
        mock_ctx.owner.username = "tester"
        mock_ctx.tg_chat.id = -100123
        mock_ctx.tg_chat.title = "Chat Test"
        mock_ctx.tg_chat.username = None
        mock_ctx.settings = mock_state.settings
        return mock_ctx

    # PRUEBAS DEL ORQUESTADOR (run_upload_flow)

    @patch("totelegram.cli.logic.prepare_upload_session")
    @patch("totelegram.cli.logic.JobPipeline")
    def test_run_upload_flow_executes_lifecycle_successfully(
        self, mock_pipeline_cls, mock_prep_ctx
    ):
        """Verifica que run_upload_flow orqueste el ciclo completo: plan_job -> open_telegram_session -> investigate -> transfer -> seal -> apply_pause."""
        mock_state = self._build_mock_cli_state()
        mock_ctx = self._build_mock_session(mock_state)
        mock_prep_ctx.return_value = mock_ctx

        mock_job_obj = MagicMock()
        mock_session = MagicMock()
        mock_report = MagicMock()
        mock_report.is_fulfilled = False
        mock_session.investigate.return_value = mock_report
        mock_session.result = JobExecutionResult(
            job=mock_job_obj,
            path=self.file_path,
            is_completed=True,
            strategy_result=StrategyResult(
                strategy_name="Mock",
                pieces_processed=1,
                did_upload_bytes=True,
                is_completed=True,
            ),
        )

        mock_pipeline = MagicMock()
        mock_pipeline_cls.return_value = mock_pipeline
        mock_pipeline.plan_job.return_value = mock_job_obj

        @contextmanager
        def fake_open_session(**kwargs):
            yield mock_session

        mock_pipeline.open_telegram_session.side_effect = fake_open_session

        mock_typer_ctx = MagicMock()
        mock_typer_ctx.obj = mock_state

        with patch(
            "totelegram.cli.logic.get_patch_status", return_value={"applied": True}
        ):
            run_upload_flow(
                ctx=mock_typer_ctx,
                paths=[self.file_path],
                target="me",
                force=False,
                yes=True,
                is_backup=False,
            )

        # Verificar llamadas del ciclo de vida
        mock_prep_ctx.assert_called_once()
        mock_pipeline.plan_job.assert_called_once_with(
            path=self.file_path,
            force=False,
            auto_truncate=False,
        )
        mock_pipeline.open_telegram_session.assert_called_once()
        self.assertEqual(
            mock_pipeline.open_telegram_session.call_args.kwargs["job"],
            mock_job_obj,
        )

        mock_session.investigate.assert_called_once()
        mock_session.transfer.assert_called_once()
        mock_session.seal.assert_called_once()
        mock_pipeline.apply_pause.assert_called_once()

    @patch("totelegram.cli.logic.prepare_upload_session")
    @patch("totelegram.cli.logic.JobPipeline")
    def test_run_upload_flow_skips_transfer_when_already_fulfilled(
        self, mock_pipeline_cls, mock_prep_ctx
    ):
        """Si el reporte indica que el archivo ya está completado, no debe transferir bytes."""
        mock_state = self._build_mock_cli_state()
        mock_ctx = self._build_mock_session(mock_state)
        mock_prep_ctx.return_value = mock_ctx

        mock_job_obj = MagicMock()
        mock_session = MagicMock()
        mock_report = MagicMock()
        mock_report.is_fulfilled = True  # Ya está completo en Telegram
        mock_session.investigate.return_value = mock_report
        mock_session.result = JobExecutionResult(
            job=mock_job_obj,
            path=self.file_path,
            is_completed=True,
            strategy_result=StrategyResult(
                strategy_name="None",
                pieces_processed=0,
                did_upload_bytes=False,
                is_completed=True,
            ),
        )

        mock_pipeline = MagicMock()
        mock_pipeline_cls.return_value = mock_pipeline
        mock_pipeline.plan_job.return_value = mock_job_obj

        @contextmanager
        def fake_open_session(**kwargs):
            yield mock_session

        mock_pipeline.open_telegram_session.side_effect = fake_open_session

        mock_typer_ctx = MagicMock()
        mock_typer_ctx.obj = mock_state

        with patch(
            "totelegram.cli.logic.get_patch_status", return_value={"applied": True}
        ):
            run_upload_flow(
                ctx=mock_typer_ctx,
                paths=[self.file_path],
                target="me",
                force=False,
                yes=True,
                is_backup=False,
            )

        mock_pipeline.plan_job.assert_called_once()
        mock_pipeline.open_telegram_session.assert_called_once()
        mock_session.investigate.assert_called_once()
        # Cuando is_fulfilled es True, sella snapshot pero NO transfiere bytes
        mock_session.transfer.assert_not_called()
        mock_session.seal.assert_called_once()

    # PRUEBAS DE DELEGACIÓN DE COMANDOS (send y backup)

    @patch("totelegram.cli.commands.send.run_upload_flow")
    def test_send_command_delegates_to_run_upload_flow(self, mock_run_flow):
        """Verifica que el comando send delegue estrictamente al runner con is_backup=False."""
        mock_ctx = MagicMock()
        send_files(
            ctx=mock_ctx,
            paths=[self.file_path],
            target="me",
            force=False,
            yes=True,
        )

        mock_run_flow.assert_called_once_with(
            ctx=mock_ctx,
            paths=[self.file_path],
            target="me",
            force=False,
            yes=True,
            is_backup=False,
        )

    @patch("totelegram.cli.commands.backup.run_upload_flow")
    def test_backup_command_delegates_to_run_upload_flow(self, mock_run_flow):
        """Verifica que el comando backup delegue al runner con is_backup=True y auto_truncate."""
        mock_ctx = MagicMock()
        backup_folders(
            ctx=mock_ctx,
            paths=[self.file_path],
            target="me",
            force=True,
            auto_truncate=True,
            yes=False,
        )

        mock_run_flow.assert_called_once_with(
            ctx=mock_ctx,
            paths=[self.file_path],
            target="me",
            force=True,
            auto_truncate=True,
            yes=False,
            is_backup=True,
        )

    # PRUEBAS UNITARIAS DE OBJETOS CON PERSONALIDAD (UploadRequest y CandidateItem)

    def test_upload_request_properties_and_cleanup_confirmation(self):
        """Valida que UploadRequest normalice opciones, etiquetas y confirmaciones."""
        mock_state = self._build_mock_cli_state()
        mock_state.settings.auto_truncate = False

        mock_ctx = MagicMock()
        mock_ctx.obj = mock_state

        # Caso Archivo con --yes (no debe pedir confirmación interactiva)
        req_file = UploadRequest.from_cli(
            ctx=mock_ctx,
            paths=[self.file_path],
            target="me",
            force=False,
            yes=True,
            is_backup=False,
            auto_truncate=None,
        )
        self.assertEqual(req_file.item_label, "archivo")
        self.assertEqual(req_file.item_plural_label, "archivos")
        self.assertFalse(req_file.auto_truncate)
        self.assertTrue(req_file.confirm_cleanup(count=3, filename="video.mp4"))

        # Caso Carpeta con confirmación interactiva (yes=False)
        req_backup = UploadRequest.from_cli(
            ctx=mock_ctx,
            paths=[self.file_path],
            target="me",
            force=True,
            yes=False,
            is_backup=True,
            auto_truncate=True,
        )
        self.assertEqual(req_backup.item_label, "carpeta")
        self.assertEqual(req_backup.item_plural_label, "carpetas")
        self.assertTrue(req_backup.auto_truncate)

        with patch("typer.confirm", return_value=True):
            self.assertTrue(req_backup.confirm_cleanup(count=2, filename="fotos"))

        with patch("typer.confirm", return_value=False):
            self.assertFalse(req_backup.confirm_cleanup(count=2, filename="fotos"))

    def test_candidate_item_properties(self):
        """Valida el cálculo de is_last y etiquetas de CandidateItem."""
        mock_engine = MagicMock()

        # Primer ítem de dos
        item_1 = CandidateItem(
            path=self.file_path,
            index=1,
            total=2,
            is_backup=False,
            inventory_engine=mock_engine,
        )
        self.assertFalse(item_1.is_last)
        self.assertEqual(item_1.label, "Archivo")

        # Segundo y último ítem de dos
        item_2 = CandidateItem(
            path=self.file_path,
            index=2,
            total=2,
            is_backup=True,
            inventory_engine=mock_engine,
        )
        self.assertTrue(item_2.is_last)
        self.assertEqual(item_2.label, "Carpeta")


if __name__ == "__main__":
    unittest.main()
