import os
import unittest
from unittest.mock import MagicMock, patch

from totelegram.cli.logic import prepare_upload_session
from totelegram.cli.state import CLIState
from totelegram.database import DatabaseSession
from totelegram.exceptions import (
    TargetNotSetError,
)
from totelegram.identity import Settings
from totelegram.models import TelegramChat
from totelegram.telegram.resolver import TargetResolver
from totelegram.telegram.types import (
    AccessReport,
    AccessStatus,
    ChatMatch,
    TargetResolutionStatus,
)


class TestTargetResolutionAndMandatoryPolicy(unittest.TestCase):
    """Batería de pruebas para la resolución determinista de destinos y política obligatoria."""

    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.mock_client = MagicMock()
        self.resolver = TargetResolver(self.mock_client, self.db)

    def tearDown(self):
        self.db_session.close()

    # --- 1. PRUEBAS DE FAST-PATHS DIRECTOS O(1) ---

    def test_direct_fast_path_me(self):
        """'me' debe resolverse directamente sin buscar en los diálogos de Telegram."""
        with patch.object(self.resolver.access_service, "verify_access") as mock_verify:
            mock_verify.return_value = AccessReport(
                status=AccessStatus.READY,
                chat=ChatMatch(id=12345, title="Saved Messages", type="private"),
                reason="OK",
            )
            res = self.resolver.resolve("me")

            self.assertEqual(res.status, TargetResolutionStatus.RESOLVED)
            self.assertEqual(res.chat_id, 12345)
            # verify_access debe llamarse con 'me' directamente
            mock_verify.assert_called_once_with("me")
            # Cero escaneos de diálogos
            self.mock_client.get_dialogs.assert_not_called()

    def test_direct_fast_path_numeric_id(self):
        """Un ID numérico (-100...) se valida directamente sin escanear diálogos."""
        with patch.object(self.resolver.access_service, "verify_access") as mock_verify:
            mock_verify.return_value = AccessReport(
                status=AccessStatus.READY,
                chat=ChatMatch(id=-100123456789, title="Canal Privado", type="channel"),
                reason="OK",
            )
            res = self.resolver.resolve("-100123456789")

            self.assertEqual(res.status, TargetResolutionStatus.RESOLVED)
            self.assertEqual(res.chat_id, -100123456789)
            mock_verify.assert_called_once_with(-100123456789)
            self.mock_client.get_dialogs.assert_not_called()

    # --- 2. PRUEBA DE ACELERACIÓN POR CACHÉ SQLITE ---

    def test_cache_sqlite_avoids_telegram_dialog_scan(self):
        """Si un chat ya existe en TelegramChat (SQLite), se resuelve a 0 ms sin buscar diálogos."""
        TelegramChat.create(
            id=-100999888,
            title="Mi Backup Frecuente",
            type="channel",
            is_public=False,
        )

        with patch.object(self.resolver.access_service, "verify_access") as mock_verify:
            mock_verify.return_value = AccessReport(
                status=AccessStatus.READY,
                chat=ChatMatch(
                    id=-100999888, title="Mi Backup Frecuente", type="channel"
                ),
                reason="OK",
            )

            res = self.resolver.resolve("Mi Backup Frecuente")

            self.assertEqual(res.status, TargetResolutionStatus.RESOLVED)
            self.assertEqual(res.chat_id, -100999888)
            # Verificó directamente el ID de la base de datos
            mock_verify.assert_called_once_with(-100999888)
            self.mock_client.get_dialogs.assert_not_called()

    # --- 3. PRUEBAS DE EXACT MATCH PRECEDENCE Y NORMALIZACIÓN DE ACENTOS ---

    def test_exact_match_precedence_beats_partial_duplicates(self):
        """
        Si existen 'Backups' y 'Backups Fotos', buscar 'Backups'
        DEBE ganar inmediatamente sin disparar error de ambigüedad.
        """
        dialog1 = MagicMock()
        dialog1.chat.id = -1001
        dialog1.chat.title = "Backups"
        dialog1.chat.username = None
        dialog1.chat.type.value = "channel"

        dialog2 = MagicMock()
        dialog2.chat.id = -1002
        dialog2.chat.title = "Backups Fotos"
        dialog2.chat.username = None
        dialog2.chat.type.value = "channel"

        self.mock_client.get_dialogs.return_value = [dialog1, dialog2]

        with patch.object(self.resolver.access_service, "verify_access") as mock_verify:
            mock_verify.return_value = AccessReport(
                status=AccessStatus.READY,
                chat=ChatMatch(id=-1001, title="Backups", type="channel"),
                reason="OK",
            )

            res = self.resolver.resolve("Backups")

            self.assertEqual(res.status, TargetResolutionStatus.RESOLVED)
            self.assertEqual(res.chat_id, -1001)

    def test_diacritics_and_case_insensitivity(self):
        """'musica' debe coincidir exactamente con un canal llamado 'Música'."""
        dialog = MagicMock()
        dialog.chat.id = -100555
        dialog.chat.title = "Música & Vídeos"
        dialog.chat.username = None
        dialog.chat.type.value = "channel"

        self.mock_client.get_dialogs.return_value = [dialog]

        with patch.object(self.resolver.access_service, "verify_access") as mock_verify:
            mock_verify.return_value = AccessReport(
                status=AccessStatus.READY,
                chat=ChatMatch(id=-100555, title="Música & Vídeos", type="channel"),
                reason="OK",
            )

            res = self.resolver.resolve("musica & videos")

            self.assertEqual(res.status, TargetResolutionStatus.RESOLVED)
            self.assertEqual(res.chat_id, -100555)

    def test_real_ambiguity_raises_ambiguous_status(self):
        """Si la búsqueda es parcial ('Foto') y coincide con 'Fotos 1' y 'Fotos 2', es ambiguo."""
        dialog1 = MagicMock()
        dialog1.chat.id = -10010
        dialog1.chat.title = "Fotos Familia"
        dialog1.chat.username = None
        dialog1.chat.type.value = "channel"

        dialog2 = MagicMock()
        dialog2.chat.id = -10020
        dialog2.chat.title = "Fotos Trabajo"
        dialog2.chat.username = None
        dialog2.chat.type.value = "channel"

        self.mock_client.get_dialogs.return_value = [dialog1, dialog2]

        res = self.resolver.resolve("Fotos")
        self.assertEqual(res.status, TargetResolutionStatus.AMBIGUOUS)
        self.assertEqual(len(res.candidates), 2)

    # --- 4. PRUEBAS DE LA POLÍTICA MANDATORIA DE DESTINO EN EL RUNNER ---

    def test_missing_target_raises_target_not_set_error(self):
        """Si no se especifica target y no hay variable de entorno, lanza TargetNotSetError."""
        mock_state = MagicMock(spec=CLIState)
        mock_settings = Settings(profile_name="demo")

        with self.assertRaises(TargetNotSetError):
            prepare_upload_session(
                state=mock_state,
                client=self.mock_client,
                db=self.db,
                settings=mock_settings,
                target_override=None,
            )

    def test_target_via_environment_variable(self):
        """Si no hay flag en CLI pero se definió TOTELEGRAM_TARGET, debe utilizarlo."""
        mock_state = MagicMock(spec=CLIState)
        mock_manager = MagicMock()
        mock_manager.node_id = "test_node"
        mock_state.manager = mock_manager  # Asignación explícita previa
        mock_settings = Settings(profile_name="demo")

        with (
            patch.dict(os.environ, {"TOTELEGRAM_TARGET": "me"}),
            patch.object(
                TargetResolver,
                "resolve",
                return_value=MagicMock(
                    status=TargetResolutionStatus.RESOLVED,
                    chat_id=123,
                    title="Saved Messages",
                ),
            ),
            patch.object(self.mock_client, "get_chat"),
            patch.object(self.mock_client, "get_me"),
        ):
            # Simulamos lo que hace Typer cuando envvar está configurado: pasa el valor del env
            env_target = os.environ.get("TOTELEGRAM_TARGET")
            session = prepare_upload_session(
                state=mock_state,
                client=self.mock_client,
                db=self.db,
                settings=mock_settings,
                target_override=env_target,
            )

            self.assertIsNotNone(session)


if __name__ == "__main__":
    unittest.main()
