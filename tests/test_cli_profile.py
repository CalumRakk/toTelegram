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

    @patch("totelegram.commands.profile.uuid")
    @patch("totelegram.commands.profile.ValidationService")
    def test_create_profile_happy_path(self, MockValidationService, mock_uuid):
        """
        Prueba el flujo completo exitoso:
        1. Genera nombre temporal.
        2. Simula creación de sesión física (Pyrogram).
        3. Valida chat OK.
        4. Renombra archivo y crea .env.
        """

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
            "-100123",
        ]
        result = runner.invoke(app, args)

        # El comando terminó bien
        self.assertEqual(result.exit_code, 0, f"Salida inesperada: {result.stdout}")
        self.assertIn("Perfil 'test_user' creado en", result.stdout)

        # El archivo temporal YA NO debe existir (fue renombrado)
        temp_path = self.mock_profiles_dir / "temp_12345678.session"
        self.assertFalse(
            temp_path.exists(), "El archivo temporal no se eliminó/renombró"
        )

        # El archivo final .session SI debe existir
        final_session = self.mock_profiles_dir / "test_user.session"
        self.assertTrue(final_session.exists(), "No se creó el archivo de sesión final")

        # El archivo .env SI debe existir
        final_env = self.mock_profiles_dir / "test_user.env"
        self.assertTrue(final_env.exists(), "No se creó el archivo .env")

        # El perfil debe estar activo
        registry = self.pm.get_registry()
        self.assertEqual(registry.active, "test_user")

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
    def test_create_profile_validation_failed_aborted(self, MockValidationService):
        """
        Simula que el Login es válido, pero el CHAT_ID es inválido.
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
        self.assertFalse(env_path.exists(), "El archivo .env no debería haberse creado")

        # Verificar que el comando terminó bien (cancelación voluntaria es exit 0)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Operación cancelada por el usuario", result.stdout)

    def test_set_config_type_safety(self):
        """
        Prueba CRÍTICA: Evitar corrupción de configuración.
        Intentar meter un string en un campo numérico debe fallar.
        """

        self.pm.create("dummy", 1, "hash", "chat")
        self.pm.activate("dummy")

        # Intentamos asignar texto a un campo entero (MAX_FILESIZE_BYTES)
        result = runner.invoke(
            app, ["set", "MAX_FILESIZE_BYTES", "esto_no_es_un_numero"]
        )

        self.assertNotEqual(
            result.exit_code, 0, "El comando debería fallar o manejar error"
        )
        self.assertIn("Error", result.stdout)

        # Verificar que el valor NO cambió en el archivo
        values = self.pm.get_config_values("dummy")
        # El valor por defecto no es ese string
        self.assertNotEqual(values.get("MAX_FILESIZE_BYTES"), "esto_no_es_un_numero")

    def test_use_profile_switching(self):
        """
        Prueba funcional: Verificar que podemos cambiar entre perfiles.
        """
        self.pm.create("perfil_A", 1, "h", "c")
        self.pm.create("perfil_B", 2, "h", "c")

        result = runner.invoke(app, ["use", "perfil_B"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.pm.active_name, "perfil_B")
        self.assertIn("Ahora usando el perfil: perfil_B", result.stdout)
