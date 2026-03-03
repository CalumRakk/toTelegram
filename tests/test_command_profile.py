import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from totelegram.cli.commands.profile import app
from totelegram.common.schemas import AccessReport, AccessStatus, ChatMatch, CLIState
from totelegram.manager.registry import SettingsManager


class TestCliProfile(unittest.TestCase):
    def setUp(self):
        """Configuración inicial antes de cada test."""
        self.runner = CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.workdir = Path(self.test_dir)

        self.manager = SettingsManager(self.workdir)
        self.manager.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.manager.inventories_dir.mkdir(parents=True, exist_ok=True)

        self.state = CLIState(manager=self.manager, profile_name=None, is_debug=False)

    def tearDown(self):
        """Limpieza después de cada test."""
        shutil.rmtree(self.test_dir)

    def test_list_profiles_empty(self):
        """Debe informar que no hay perfiles si la carpeta está vacía."""
        result = self.runner.invoke(app, ["list"], obj=self.state)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No se encontraron perfiles", result.stdout)

    @patch("totelegram.telegram.auth.AuthLogic.initialize_profile")
    @patch("totelegram.telegram.client.TelegramSession.from_profile")
    @patch("totelegram.telegram.access.ChatAccessService.verify_access")
    def test_create_profile_success_with_chat_id(
        self, mock_verify, mock_session, mock_auth
    ):
        """Prueba la creación de un perfil con chat_id directo (sin wizard)."""

        profile_name = "test_profile"

        # Simulamos que el acceso al chat es válido
        mock_verify.return_value = AccessReport(
            status=AccessStatus.READY,
            chat=ChatMatch(id=-100123456, title="Destino Test", type="channel"),
            reason="Acceso verificado",
        )

        # Ejecutamos el comando pasando argumentos para evitar prompts interactivos
        result = self.runner.invoke(
            app,
            [
                "create",
                "--profile-name",
                profile_name,
                "--api-id",
                "123456",
                "--api-hash",
                "abcdef",
                "--chat-id",
                "-100123456",
            ],
            obj=self.state,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn(f"Perfil '{profile_name}' creado exitosamente", result.stdout)

        # Verificamos que se guardó la configuración en el .env
        settings = self.manager._load_and_sanitize(profile_name)
        self.assertEqual(settings["chat_id"], -100123456)

    def test_switch_profile_fails_if_incomplete(self):
        """No debe permitir cambiar a un perfil que no sea 'trinity' (incompleto)."""
        profile_name = "orphan"

        # Creamos solo el .env, pero no el archivo .session
        env_path = self.manager.get_settings_path(profile_name)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("profile_name=orphan")

        result = self.runner.invoke(app, ["switch", profile_name], obj=self.state)

        self.assertEqual(result.exit_code, 1)
        self.assertIn("está incompleto", result.stdout)

    def test_switch_profile_success(self):
        """Cambio exitoso de perfil cuando existen .env y .session."""
        profile_name = "active_user"

        # 1. Crear .env
        env_path = self.manager.get_settings_path(profile_name)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("profile_name=active_user\nchat_id=12345")

        # 2. Crear .session (archivo vacío para simular existencia)
        session_path = self.manager.get_session_path(profile_name)
        session_path.write_text("fake session data")

        result = self.runner.invoke(app, ["switch", profile_name], obj=self.state)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Perfil cambiado exitosamente", result.stdout)
        self.assertEqual(self.manager.get_active_profile_name(), profile_name)

    def test_delete_profile_full(self):
        """Eliminación física de un perfil."""
        profile_name = "target_delete"

        # Crear rastro
        env_path = self.manager.get_settings_path(profile_name)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("data")
        session_path = self.manager.get_session_path(profile_name)
        session_path.write_text("data")

        # Usamos --yes para saltar la confirmación interactiva
        result = self.runner.invoke(
            app, ["delete", profile_name, "--yes"], obj=self.state
        )

        self.assertEqual(result.exit_code, 0)
        self.assertFalse(env_path.exists())
        self.assertFalse(session_path.exists())
        self.assertIn(f"Perfil '{profile_name}' eliminado", result.stdout)


if __name__ == "__main__":
    unittest.main()
