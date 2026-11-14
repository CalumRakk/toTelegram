import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from totelegram.cli.views.errors import CLIExceptionHandler
from totelegram.exceptions import (
    AccountBusyError,
    IncompatibleCapacityError,
    NoPendingCandidatesError,
    PathLengthExceededError,
    PermissionDeniedCleanupError,
    PipelineCancelledError,
    SystemSuspendedError,
    TargetAmbiguousError,
    TargetNotSetError,
    UnsupportedTargetTypeError,
)
from totelegram.i18n import override_locale


class TestExceptionsI18n(unittest.TestCase):
    """Pruebas de internacionalización y gramática para las excepciones de dominio (Capa 2)."""

    def test_no_pending_candidates_error_localization(self):
        """Valida que NoPendingCandidatesError resuelva oraciones completas sin concatenar 's'."""
        # 1. Caso Archivo sin destino
        with override_locale("es"):
            err_es = NoPendingCandidatesError(item_type="archivo")
            self.assertEqual(
                err_es.message, "No se encontraron archivos pendientes para subir."
            )
            self.assertIn("Todos los elementos ya están respaldados", err_es.hint)

        with override_locale("en"):
            err_en = NoPendingCandidatesError(item_type="archivo")
            self.assertEqual(err_en.message, "No pending files found to upload.")
            self.assertIn("All items are already backed up", err_en.hint)

        # 2. Caso Carpeta con destino especificado
        with override_locale("es"):
            err_folder_es = NoPendingCandidatesError(
                item_type="carpeta", destination_title="Mis Respaldos"
            )
            self.assertEqual(
                err_folder_es.message,
                "No se encontraron carpetas pendientes para subir para el destino 'Mis Respaldos'.",
            )

        with override_locale("en"):
            err_folder_en = NoPendingCandidatesError(
                item_type="folder", destination_title="My Backups"
            )
            self.assertEqual(
                err_folder_en.message,
                "No pending folders found to upload for destination 'My Backups'.",
            )

    def test_target_errors_localization(self):
        """Valida excepciones de configuración de chat destino (Target)."""
        # TargetNotSetError
        with override_locale("es"):
            err = TargetNotSetError()
            self.assertIn("No indicaste el destino", err.message)
            self.assertIn("-t me", err.message)

        with override_locale("en"):
            err = TargetNotSetError()
            self.assertIn("No destination specified", err.message)
            self.assertIn("-t me", err.message)

        # TargetAmbiguousError
        mock_candidates = [
            MagicMock(id=101, title="Canal Respaldos"),
            MagicMock(id=102, title="Canal Respaldos 2"),
        ]

        with override_locale("es"):
            err_amb_es = TargetAmbiguousError("respaldos", mock_candidates)
            self.assertEqual(err_amb_es.title, "Destino Ambiguo")
            self.assertIn("2 coincidencias", err_amb_es.message)
            self.assertIn('# Para: "Canal Respaldos"', err_amb_es.suggested_commands[0])

        with override_locale("en"):
            err_amb_en = TargetAmbiguousError("respaldos", mock_candidates)
            self.assertEqual(err_amb_en.title, "Ambiguous Target")
            self.assertIn("2 matches found", err_amb_en.message)
            self.assertIn('# For: "Canal Respaldos"', err_amb_en.suggested_commands[0])

    def test_incompatible_capacity_error_localization(self):
        """Valida mensajes de incompatibilidad Premium vs Estándar y sugerencias."""
        chunk_4gb = 4000 * 1024 * 1024
        limit_2gb = 2000 * 1024 * 1024

        with override_locale("es"):
            err_es = IncompatibleCapacityError(
                job_chunk_size=chunk_4gb,
                account_max_size=limit_2gb,
                is_premium=False,
            )
            self.assertEqual(err_es.title, "Incompatibilidad de Capacidad")
            self.assertIn("la cuenta activa actual es Estándar", err_es.message)
            self.assertIn("4000 MB", err_es.message)
            self.assertIn("2000 MB", err_es.message)
            # Validar que los comandos ejecutables son idénticos pero los comentarios difieren
            self.assertTrue(
                err_es.suggested_commands[0].startswith("totelegram switch")
            )
            self.assertIn(
                "# Cambiar a una cuenta con capacidad suficiente",
                err_es.suggested_commands[0],
            )

        with override_locale("en"):
            err_en = IncompatibleCapacityError(
                job_chunk_size=chunk_4gb,
                account_max_size=limit_2gb,
                is_premium=False,
            )
            self.assertEqual(err_en.title, "Capacity Incompatibility")
            self.assertIn("active account is Standard", err_en.message)
            self.assertIn(
                "# Switch to an account with sufficient capacity",
                err_en.suggested_commands[0],
            )

    def test_unsupported_target_type_error_localization(self):
        """Valida que los tipos de chats de Telegram se traduzcan adecuadamente."""
        with override_locale("es"):
            err_es = UnsupportedTargetTypeError(
                chat_id=-100123, chat_title="Mi Grupo", chat_type="group"
            )
            self.assertEqual(err_es.title, "Destino No Compatible")
            self.assertIn("es de tipo [bold magenta]Grupo[/]", err_es.message)
            self.assertIn(
                "Crea un canal privado en Telegram", err_es.suggested_commands[0]
            )

        with override_locale("en"):
            err_en = UnsupportedTargetTypeError(
                chat_id=-100123, chat_title="My Group", chat_type="group"
            )
            self.assertEqual(err_en.title, "Unsupported Target")
            self.assertIn("is of type [bold magenta]Group[/]", err_en.message)
            self.assertIn(
                "Create a private channel on Telegram", err_en.suggested_commands[0]
            )

    def test_infrastructure_and_concurrency_errors_localization(self):
        """Valida errores de permisos, cuenta ocupada y suspensión."""
        with override_locale("es"):
            err_perm_es = PermissionDeniedCleanupError(chat_id=-100999)
            self.assertEqual(err_perm_es.title, "Permisos Insuficientes en Telegram")
            self.assertIn("Eliminar mensajes", err_perm_es.message)

            err_busy_es = AccountBusyError(account_id=12345, holder_node_id="pc-vps:99")
            self.assertEqual(err_busy_es.title, "Cuenta en uso")
            self.assertIn("nodo: pc-vps:99", err_busy_es.message)

            err_susp_es = SystemSuspendedError()
            self.assertEqual(err_susp_es.title, "Sistema Suspendido")
            self.assertIn("hibernó", err_susp_es.message)

        with override_locale("en"):
            err_perm_en = PermissionDeniedCleanupError(chat_id=-100999)
            self.assertEqual(err_perm_en.title, "Insufficient Telegram Permissions")
            self.assertIn("Delete messages", err_perm_en.message)

            err_busy_en = AccountBusyError(account_id=12345, holder_node_id="pc-vps:99")
            self.assertEqual(err_busy_en.title, "Account in Use")
            self.assertIn("node: pc-vps:99", err_busy_en.message)

            err_susp_en = SystemSuspendedError()
            self.assertEqual(err_susp_en.title, "System Suspended")
            self.assertIn("hibernated", err_susp_en.message)

    def test_rich_markup_tags_balance(self):
        """Asegura que las etiquetas de Rich [tag]...[/] abran y cierren sin corromper la terminal."""
        sample_exceptions = [
            TargetNotSetError(),
            UnsupportedTargetTypeError(-100, "Test", "supergroup"),
        ]

        tag_pattern = re.compile(r"\[/?([a-zA-Z0-9_# =]+)\]")

        for lang in ("es", "en"):
            with override_locale(lang):
                for exc in sample_exceptions:
                    text = f"{exc.message} {exc.hint or ''}"
                    tags = tag_pattern.findall(text)
                    # Comprobamos que no queden corchetes huérfanos o asimétricos
                    open_brackets = text.count("[")
                    close_brackets = text.count("]")
                    self.assertEqual(
                        open_brackets,
                        close_brackets,
                        f"Desbalance de corchetes Rich en {exc.__class__.__name__} [{lang}]: {text}",
                    )

    @patch("totelegram.cli.ui.UI.educational_tip")
    @patch("totelegram.cli.ui.UI.info")
    def test_cli_exception_handler_rendering(self, mock_info, mock_tip):
        """Verifica que el presentador CLIExceptionHandler renderice excepciones localizadas sin error."""
        with override_locale("en"):
            # Excepción rica con título y comandos sugeridos
            rich_err = TargetAmbiguousError("channel", [MagicMock(id=1, title="Chan")])
            CLIExceptionHandler.render(rich_err)
            mock_tip.assert_called_once()
            self.assertEqual(mock_tip.call_args.kwargs["title"], "Ambiguous Target")

            # Excepción simple tipo aviso (severity="info")
            abort_err = PipelineCancelledError()
            CLIExceptionHandler.render(abort_err)
            mock_info.assert_called_once_with(
                "Operation cancelled by user.", spacing="top"
            )


if __name__ == "__main__":
    unittest.main()
