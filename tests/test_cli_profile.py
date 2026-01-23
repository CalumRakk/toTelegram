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
        self.tmp_dir = TemporaryDirectory()
        self.config_path = Path(self.tmp_dir.name)
        self.pm = ProfileManager(base_dir=self.config_path)
        self.pm.create("test_user", 12345, "fake_hash", "123456789")
        self.pm.activate("test_user")

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch("totelegram.commands.profile.ValidationService")
    @patch("totelegram.commands.profile.uuid")
    def test_create_profile_happy_path(self, mock_uuid, MockValidator):
        # Fijamos el nombre del archivo temporal
        mock_uuid.uuid4.return_value.hex = "12345678"
        temp_session_name = "temp_12345678"

        @contextmanager
        def side_effect_validate_session(session_name, api_id, api_hash):
            # El comando espera que se cree un archivo .session
            temp_file = self.pm.profiles_dir / f"{session_name}.session"
            temp_file.parent.mkdir(parents=True, exist_ok=True)
            temp_file.touch()

            yield MagicMock()

        instance = MockValidator.return_value
        instance.validate_session.side_effect = side_effect_validate_session

        with patch("totelegram.commands.profile.TelegramSession"), patch(
            "totelegram.commands.profile.resolve_and_store_chat_logic"
        ) as mock_resolve:

            mock_resolve.return_value = True

            # Input: Nombre real, API_ID, API_HASH, Wizard Opción 4 (Omitir)
            result = runner.invoke(
                app, ["create"], input="perfil_final\n111\nshhh\n4\n", obj=self.pm
            )

        # Verificaciones
        self.assertEqual(result.exit_code, 0)

        # Comprobamos que el archivo fue renombrado correctamente por el comando
        final_path = self.pm.profiles_dir / "perfil_final.session"
        self.assertTrue(
            final_path.exists(), "El comando deberia haber renombrado el temporal"
        )

        # Comprobamos que no quedo rastro del temporal
        temp_path = self.pm.profiles_dir / f"{temp_session_name}.session"
        self.assertFalse(
            temp_path.exists(), "El archivo temporal deberia haber desaparecido"
        )

    @patch("totelegram.commands.profile.TelegramSession")
    @patch("totelegram.commands.profile.ValidationService")
    @patch("totelegram.commands.profile.uuid")
    def test_create_profile_validation_failed_aborted(
        self, mock_uuid, MockTgProfile, MockValidationService
    ):
        """
        Simula que el Login es válido, pero el usuario elige Omitir el chat.
        """
        mock_client = MagicMock()
        MockTgProfile.return_value.__enter__.return_value = mock_client

        instance = MockValidationService.return_value
        mock_uuid.uuid4.return_value.hex = "3rdfg"
        instance = MockValidationService.return_value
        temp_session = self.pm.profiles_dir / "temp_3rdfg.session"
        temp_session.parent.mkdir(parents=True, exist_ok=True)
        temp_session.touch()

        # Simulamos éxito en login pero luego el usuario cancela en el wizard
        @contextmanager
        def side_effect_validate_session(session_name, api_id, api_hash):
            self.pm.profiles_dir.mkdir(parents=True, exist_ok=True)
            (self.pm.profiles_dir / f"{session_name}.session").touch()
            yield mock_client

        instance.validate_session.side_effect = side_effect_validate_session

        # Inputs: Name, API_ID, API_HASH, Wizard Option 4
        inputs = "bad_user\n12345\nfake_hash\n4\n"  # 4 = Omitir
        result = runner.invoke(app, ["create"], input=inputs, obj=self.pm)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("creado, pero sin destino configurado", result.stdout)

        # El archivo DEBE existir (ADR-005)
        self.assertTrue((self.pm.profiles_dir / "bad_user.env").exists())

    def test_use_profile_switching(self):
        """
        Prueba funcional: Verificar que podemos cambiar entre perfiles.
        """
        self.pm.create("perfil_A", 1, "h", "c")
        self.pm.create("perfil_B", 2, "h", "c")

        result = runner.invoke(app, ["switch", "perfil_B"], obj=self.pm)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.pm.active_name, "perfil_B")
        self.assertIn("Ahora usando el perfil", result.stdout)
        self.assertIn("perfil_B", result.stdout)

    def test_create_profile_prevents_overwrite(self):
        """
        Verifica que no se puede crear un perfil si ya existe (Inmutabilidad).
        """
        # Crea un perfil que ya existe
        nombre_duplicado = "usuario_antiguo"
        self.pm.create(nombre_duplicado, 12345, "hash_original", "chat_original")

        # Typer ejecutará el callback 'validate_profile_name' inmediatamente
        result = runner.invoke(
            app,
            [
                "create",
                "--profile-name",
                nombre_duplicado,
                "--api-id",
                "99999",
                "--api-hash",
                "nuevo_hash_que_no_debe_guardarse",
            ],
            obj=self.pm,
        )

        # El comando debe terminar con un código de error (distinto de 0)
        self.assertNotEqual(result.exit_code, 0)

        self.assertIn(f"perfil '{nombre_duplicado}' ya existe", result.stdout)

        # el perfil original debe seguir intacto
        values = self.pm.get_config_values(nombre_duplicado)
        self.assertEqual(values["API_ID"], "12345")
        self.assertEqual(values["API_HASH"], "hash_original")
