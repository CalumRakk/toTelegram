import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from totelegram.commands.profile import app
from totelegram.core.registry import ProfileManager

runner = CliRunner()


class TestCliProfile(unittest.TestCase):
    def setUp(self):
        self.test_dir_obj = TemporaryDirectory()
        self.test_dir = Path(self.test_dir_obj.name)
        fake_profiles_dir = self.test_dir / "profiles"

        self.patcher_config_dir = patch(
            "totelegram.core.registry.CONFIG_DIR", self.test_dir
        )
        self.patcher_config_file = patch(
            "totelegram.core.registry.ProfileManager.CONFIG_FILE",
            self.test_dir / "config.json",
        )

        self.patcher_profiles = patch(
            "totelegram.core.registry.ProfileManager.PROFILES_DIR", fake_profiles_dir
        )

        self.mock_config_dir = self.patcher_config_dir.start()
        self.mock_config_file = self.patcher_config_file.start()
        self.mock_profiles_dir = self.patcher_profiles.start()

        self.mock_profiles_dir.mkdir(parents=True, exist_ok=True)
        self.pm = ProfileManager()

    def tearDown(self):
        self.patcher_profiles.stop()
        self.test_dir_obj.cleanup()

    @patch("totelegram.commands.profile.TelegramSession")
    @patch("totelegram.commands.config.TelegramSession")
    @patch("totelegram.commands.profile.uuid")
    @patch("totelegram.commands.profile.ValidationService")
    def test_create_profile_happy_path(
        self, MockValidationService, mock_uuid, MockTgConfig, MockTgProfile
    ):
        """
        Prueba el flujo completo exitoso:
        1. Genera nombre temporal.
        2. Simula creación de sesión física (Pyrogram).
        3. Valida chat OK.
        4. Renombra archivo y crea .env.
        """

        mock_client = MagicMock()
        MockTgConfig.return_value.__enter__.return_value = mock_client
        MockTgProfile.return_value.__enter__.return_value = mock_client

        # El temp será "temp_12345678.session"
        mock_uuid.uuid4.return_value.hex = "12345678"
        instance = MockValidationService.return_value

        # Simulamos el context manager de validate_session
        @contextmanager
        def side_effect_validate_session(session_name, api_id, api_hash):
            fake_session_path = self.mock_profiles_dir / f"{session_name}.session"
            fake_session_path.touch()
            yield MagicMock()

        instance.validate_session.side_effect = side_effect_validate_session
        instance.validate_chat_id.return_value = True

        args = [
            "create",
            "--profile-name",
            "test_user",
            "--api-id",
            "11111",
            "--api-hash",
            "fake_hash_123",
            "--chat-id",
            "me",
        ]
        result = runner.invoke(app, args)

        self.assertEqual(result.exit_code, 0, f"Salida inesperada: {result.stdout}")
        self.assertTrue((self.mock_profiles_dir / "test_user.session").exists())
        self.assertTrue((self.mock_profiles_dir / "test_user.env").exists())

    def test_create_profile_prevents_overwrite(self):
        """
        Verifica que no se puede crear un perfil si ya existe (Inmutabilidad).
        """
        # Crear un perfil previo
        self.pm.create("dummy", 1, "h", "c")

        # Intentar crear uno con el mismo nombre
        args = [
            "create",
            "--profile-name",
            "dummy",
            "--api-id",
            "999",
            "--api-hash",
            "new_hash",
            "--chat-id",
            "new_chat",
        ]

        # La entrada "dummy" al prompt debería fallar inmediatamente o el callback
        result = runner.invoke(app, args)

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("ya existe", result.stdout)

    @patch("totelegram.commands.profile.ValidationService")
    @patch("totelegram.commands.profile.TelegramSession")
    @patch("totelegram.commands.config.TelegramSession")
    def test_create_profile_validation_failed_aborted(
        self, MockTgConfig, MockTgProfile, MockValidationService
    ):
        """
        Simula que el Login es válido, pero el usuario elige Omitir el chat.
        """
        mock_client = MagicMock()
        MockTgConfig.return_value.__enter__.return_value = mock_client
        MockTgProfile.return_value.__enter__.return_value = mock_client

        instance = MockValidationService.return_value

        # Simulamos éxito en login pero luego el usuario cancela en el wizard
        @contextmanager
        def side_effect_validate_session(session_name, api_id, api_hash):
            (self.mock_profiles_dir / f"{session_name}.session").touch()
            yield mock_client

        instance.validate_session.side_effect = side_effect_validate_session

        # Inputs: Name, API_ID, API_HASH, Wizard Option 4
        inputs = "bad_user\n12345\nfake_hash\n4\n"  # 4 = Omitir
        result = runner.invoke(app, ["create"], input=inputs)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("creado, pero sin destino configurado", result.stdout)

        # El archivo DEBE existir (ADR-005)
        self.assertTrue((self.mock_profiles_dir / "bad_user.env").exists())

    def test_use_profile_switching(self):
        """
        Prueba funcional: Verificar que podemos cambiar entre perfiles.
        """
        self.pm.create("perfil_A", 1, "h", "c")
        self.pm.create("perfil_B", 2, "h", "c")

        result = runner.invoke(app, ["switch", "perfil_B"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.pm.active_name, "perfil_B")
        self.assertIn("Ahora usando el perfil", result.stdout)
        self.assertIn("perfil_B", result.stdout)
