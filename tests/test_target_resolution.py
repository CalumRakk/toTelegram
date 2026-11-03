import os
import unittest
from unittest.mock import MagicMock, patch

from pyrogram.enums import ChatType

from totelegram.cli.logic import prepare_upload_session
from totelegram.cli.state import CLIState
from totelegram.database import DatabaseSession
from totelegram.exceptions import (
    TargetNotSetError,
    UnsupportedTargetTypeError,
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

    def test_reject_supergroup_and_group(self):
        # 1. Supergrupo
        mock_sg = MagicMock()
        mock_sg.id = -100999000
        mock_sg.title = "Comunidad de Películas"
        mock_sg.type = ChatType.SUPERGROUP
        mock_sg.username = None
        self.mock_client.get_chat.return_value = mock_sg

        res = self.resolver.resolve("-100999000")
        self.assertEqual(res.status, TargetResolutionStatus.UNSUPPORTED_TYPE)
        self.assertIn("Supergrupo", res.error_message)

        # 2. Grupo básico
        mock_group = MagicMock()
        mock_group.id = -123456
        mock_group.title = "Amigos"
        mock_group.type = ChatType.GROUP
        mock_group.username = None
        self.mock_client.get_chat.return_value = mock_group

        res_group = self.resolver.resolve("-123456")
        self.assertEqual(res_group.status, TargetResolutionStatus.UNSUPPORTED_TYPE)
        self.assertIn("Grupo", res_group.error_message)

    def test_reject_foreign_private_chat(self):
        mock_private = MagicMock()
        mock_private.id = 99887766
        mock_private.title = None
        mock_private.first_name = "Juan Pérez"
        mock_private.type = ChatType.PRIVATE
        mock_private.username = "juanperez"

        self.mock_client.get_chat.return_value = mock_private
        self.mock_client.get_me.return_value = MagicMock(id=12345)

        res = self.resolver.resolve("99887766")
        self.assertEqual(res.status, TargetResolutionStatus.UNSUPPORTED_TYPE)
        self.assertIn("chat privado ajeno", res.error_message)

    def test_channel_missing_post_permission_is_restricted(self):
        """Si la cuenta es administradora pero carece de 'can_post_messages', debe ser restringida."""
        mock_channel = MagicMock()
        mock_channel.id = -100555666
        mock_channel.title = "Canal Noticias"
        mock_channel.type = ChatType.CHANNEL
        mock_channel.username = None

        self.mock_client.get_chat.return_value = mock_channel

        # Simulamos que es miembro pero sin permisos de publicación
        mock_member = MagicMock()
        mock_member.status = MagicMock()
        mock_member.status.value = "administrator"
        mock_member.privileges = MagicMock(can_post_messages=False)
        self.mock_client.get_chat_member.return_value = mock_member

        res = self.resolver.resolve("-100555666")
        self.assertEqual(res.status, TargetResolutionStatus.NO_PERMISSIONS)
        self.assertIn("permisos de administrador para publicar", res.error_message)

    def test_search_dialogs_strictly_ignores_groups(self):
        """
        Si existen un grupo y un canal con el mismo nombre ('Respaldos'),
        el buscador debe ignorar el grupo por completo y resolver el canal sin ambigüedad.
        """
        # Diálogo 1: Grupo
        d_group = MagicMock()
        d_group.chat.id = -100111
        d_group.chat.title = "Respaldos"
        d_group.chat.type = ChatType.SUPERGROUP
        d_group.chat.username = None

        # Diálogo 2: Canal
        d_channel = MagicMock()
        d_channel.chat.id = -100222
        d_channel.chat.title = "Respaldos"
        d_channel.chat.type = ChatType.CHANNEL
        d_channel.chat.username = None

        self.mock_client.get_dialogs.return_value = [d_group, d_channel]

        with patch.object(self.resolver.access_service, "verify_access") as mock_verify:
            mock_verify.return_value = MagicMock(
                is_ready=True,
                chat=ChatMatch(id=-100222, title="Respaldos", type="channel"),
            )
            res = self.resolver.resolve("Respaldos")

            self.assertEqual(res.status, TargetResolutionStatus.RESOLVED)
            self.assertEqual(res.chat_id, -100222)

    def test_prepare_upload_session_raises_unsupported_target_type(self):
        mock_state = MagicMock(spec=CLIState)
        mock_settings = Settings(profile_name="demo")

        mock_group = MagicMock()
        mock_group.id = -100999
        mock_group.title = "Mi Grupo"
        mock_group.type = ChatType.SUPERGROUP
        mock_group.username = None
        self.mock_client.get_chat.return_value = mock_group

        with self.assertRaises(UnsupportedTargetTypeError) as ctx:
            prepare_upload_session(
                state=mock_state,
                client=self.mock_client,
                db=self.db,
                settings=mock_settings,
                target_override="-100999",
            )

        self.assertIn("Supergrupo", ctx.exception.message)
        self.assertEqual(ctx.exception.chat_id, -100999)


if __name__ == "__main__":
    unittest.main()
