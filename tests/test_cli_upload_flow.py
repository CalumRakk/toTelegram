import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from totelegram.cli.commands.backup import backup_folders
from totelegram.cli.commands.send import send_files
from totelegram.cli.logic import run_upload_flow
from totelegram.cli.state import CLIState
from totelegram.identity import SettingsManager
from totelegram.types import JobExecutionResult, StrategyResult

runner = CliRunner()


class TestCliUploadFlow(unittest.TestCase):
    """Pruebas del runner unificado de subidas (Fase 1.1)."""

    def setUp(self):
        self.tmp_file = NamedTemporaryFile(delete=False)  # noqa: SIM115
        self.tmp_file.write(b"DATA_UPLOAD_TEST" * 50)
        self.tmp_file.close()
        self.file_path = Path(self.tmp_file.name)

    def tearDown(self):
        if self.file_path.exists():
            self.file_path.unlink()

    @patch("totelegram.cli.logic.prepare_upload_context")
    @patch("totelegram.cli.logic.JobPipeline")
    def test_run_upload_flow_executes_successfully(
        self, mock_pipeline_cls, mock_prep_ctx
    ):
        """Verifica que run_upload_flow orqueste el inventario y el pipeline correctamente."""
        mock_ctx = MagicMock()
        mock_ctx.owner.first_name = "TestOwner"
        mock_ctx.owner.username = "tester"
        mock_ctx.tg_chat.id = -100123
        mock_ctx.tg_chat.title = "Chat Test"
        mock_ctx.tg_chat.username = None
        mock_prep_ctx.return_value = mock_ctx

        mock_pipeline = MagicMock()
        mock_pipeline_cls.return_value = mock_pipeline

        mock_pipeline.process.return_value = JobExecutionResult(
            job=MagicMock(),
            path=self.file_path,
            is_completed=True,
            strategy_result=StrategyResult(
                strategy_name="Mock",
                pieces_processed=1,
                did_upload_bytes=True,
                is_completed=True,
            ),
        )

        mock_state = MagicMock(spec=CLIState)
        mock_state.profile_name = "test_profile"
        mock_state.manager = MagicMock(spec=SettingsManager)
        mock_state.manager.resolve_profile_name.return_value = "test_profile"

        # Context manager para state.scope() que devuelve la tupla esperada (client, db)
        @contextmanager
        def fake_scope():
            yield MagicMock(), MagicMock()

        mock_state.scope = fake_scope

        mock_settings = MagicMock()
        mock_settings.has_custom_app = True
        mock_settings.auto_truncate = False
        mock_settings.exclude_files = []
        mock_settings.max_filesize_bytes = 10**9
        mock_state.manager.get_settings.return_value = mock_settings

        mock_typer_ctx = MagicMock()
        mock_typer_ctx.obj = mock_state

        # Ejecutamos el runner unificado
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

        # Verificaciones
        mock_prep_ctx.assert_called_once()
        mock_pipeline.process.assert_called_once()
        self.assertEqual(mock_pipeline.process.call_args.kwargs["path"], self.file_path)

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


if __name__ == "__main__":
    unittest.main()
