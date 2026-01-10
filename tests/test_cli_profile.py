import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from totelegram.commands.profile import app
from totelegram.core.profiles import ProfileManager

runner = CliRunner()


class TestCliProfile(unittest.TestCase):
    def setUp(self):
        self.test_dir_obj = TemporaryDirectory()
        self.test_dir = Path(self.test_dir_obj.name)

        self.patcher_config = patch(
            "totelegram.core.profiles.CONFIG_DIR", self.test_dir
        )
        self.patcher_profiles = patch(
            "totelegram.core.profiles.PROFILES_DIR", self.test_dir / "profiles"
        )
        self.patcher_file = patch(
            "totelegram.core.profiles.CONFIG_FILE", self.test_dir / "config.json"
        )

        self.mock_config_dir = self.patcher_config.start()
        self.mock_profiles_dir = self.patcher_profiles.start()
        self.mock_config_file = self.patcher_file.start()

        self.pm = ProfileManager()

    def tearDown(self):
        self.patcher_config.stop()
        self.patcher_profiles.stop()
        self.patcher_file.stop()
        self.test_dir_obj.cleanup()

    @patch("totelegram.commands.profile.ValidationService")
    def test_create_profile_happy_path(self, MockValidationService):
        """
        Crear un perfil válido.
        Simulamos que ValidationService devuelve True (Telegram validó OK).
        """
        instance = MockValidationService.return_value
        instance.validate_setup.return_value = True

        # inputs del usuario: Nombre, API_ID, API_HASH, CHAT_ID, Confirmar activación
        inputs = "test_user\n12345\nabcdef\n-100123\ny\n"

        result = runner.invoke(app, ["create"], input=inputs)

        # Verificar que el comando terminó bien
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Perfil 'test_user' guardado exitosamente", result.stdout)

        # Verificar que el archivo .env existe físicamente
        env_path = self.mock_profiles_dir / "test_user.env"
        self.assertTrue(env_path.exists())

        # Verificar que se marcó como activo
        registry = self.pm.list_profiles()
        self.assertEqual(registry.active, "test_user")

    @patch("totelegram.commands.profile.ValidationService")
    def test_create_profile_validation_failed_aborted(self, MockValidationService):
        """
        Prueba CRÍTICA: Simula que el Login es válido, pero el CHAT_ID es inválido.
        El usuario decide NO guardar el perfil.
        """
        instance = MockValidationService.return_value

        mock_client = MagicMock()
        instance.validate_session.return_value.__enter__.return_value = mock_client

        # Simular que la validación del CHAT_ID falla (devuelve False)
        instance.validate_chat_id.return_value = False


        inputs = "bad_user\n12345\nfake_hash\n-100123\nn\n"
        result = runner.invoke(app, ["create"], input=inputs)

        # Verificar que NO se creó el archivo
        env_path = self.mock_profiles_dir / "bad_user.env"
        self.assertFalse(env_path.exists())

        # Verificar que el comando terminó bien (cancelación voluntaria es exit 0)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Operación cancelada", result.stdout)

    def test_set_config_type_safety(self):
        """
        Prueba CRÍTICA: Evitar corrupción de configuración.
        Intentar meter un string en un campo numérico debe fallar.
        """

        self.pm.create_profile("dummy", 1, "hash", "chat")
        self.pm.set_active("dummy")

        # Intentamos asignar texto a un campo entero (MAX_FILESIZE_BYTES)
        result = runner.invoke(
            app, ["set", "MAX_FILESIZE_BYTES", "esto_no_es_un_numero"]
        )

        self.assertNotEqual(
            result.exit_code, 0, "El comando debería fallar o manejar error"
        )
        self.assertIn("Error", result.stdout)

        # Verificar que el valor NO cambió en el archivo
        values = self.pm.get_profile_values("dummy")
        # El valor por defecto no es ese string
        self.assertNotEqual(values.get("MAX_FILESIZE_BYTES"), "esto_no_es_un_numero")

    def test_use_profile_switching(self):
        """
        Prueba funcional: Verificar que podemos cambiar entre perfiles.
        """
        self.pm.create_profile("perfil_A", 1, "h", "c")
        self.pm.create_profile("perfil_B", 2, "h", "c")

        result = runner.invoke(app, ["use", "perfil_B"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.pm.get_name_active_profile(), "perfil_B")
        self.assertIn("Ahora usando el perfil: perfil_B", result.stdout)
