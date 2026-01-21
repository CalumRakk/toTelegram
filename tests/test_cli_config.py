import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from typer.testing import CliRunner

from totelegram.commands.config import app
from totelegram.core.registry import ProfileManager

runner = CliRunner()


class TestCliConfig(unittest.TestCase):
    def setUp(self):
        # Crear un entorno virtual aislado para no romper tu config real
        self.tmp_dir = TemporaryDirectory()
        self.config_path = Path(self.tmp_dir.name)

        # Parchear las rutas de ProfileManager para que apunten al temporal
        self.patch_dir = patch("totelegram.core.registry.CONFIG_DIR", self.config_path)
        self.patch_profiles = patch(
            "totelegram.core.registry.ProfileManager.PROFILES_DIR",
            self.config_path / "profiles",
        )
        self.patch_file = patch(
            "totelegram.core.registry.ProfileManager.CONFIG_FILE",
            self.config_path / "config.json",
        )

        self.patch_dir.start()
        self.patch_profiles.start()
        self.patch_file.start()

        # Inicializar el manager en el entorno limpio
        self.pm = ProfileManager()
        self.pm.create("test_user", 12345, "fake_hash", "123456789")
        self.pm.activate("test_user")

    def tearDown(self):
        self.patch_dir.stop()
        self.patch_profiles.stop()
        self.patch_file.stop()
        self.tmp_dir.cleanup()

    def test_config_display_table(self):
        """Verificar que el comando base muestra la tabla de configuración."""
        result = runner.invoke(app)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Configuración", result.stdout)
        self.assertIn("test_user", result.stdout)
        self.assertIn("CHAT_ID", result.stdout)

    def test_set_valid_value(self):
        """Probar que un cambio válido se persiste correctamente."""
        # Cambiamos el límite de subida
        result = runner.invoke(app, ["set", "upload_limit_rate_kbps", "500"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("UPLOAD_LIMIT_RATE_KBPS -> '500'", result.stdout)

        # Verificar permanencia en el .env
        values = self.pm.get_config_values("test_user")
        self.assertEqual(values["UPLOAD_LIMIT_RATE_KBPS"], "500")

    def test_set_invalid_type_safety(self):
        """
        Si meto basura en un campo numérico,
        Pydantic debe saltar y el comando debe fallar.
        """
        # MAX_FILESIZE_BYTES espera un entero
        result = runner.invoke(app, ["set", "MAX_FILESIZE_BYTES", "no_soy_un_numero"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Error", result.stdout)

        # El valor original no debería haber cambiado
        values = self.pm.get_config_values("test_user")
        self.assertNotEqual(values.get("MAX_FILESIZE_BYTES"), "no_soy_un_numero")

    def test_set_internal_field_protection(self):
        """
        ADR-001: API_ID y otros campos internos son inmutables tras la creación.
        """
        result = runner.invoke(app, ["set", "API_ID", "99999"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("es interna y no se puede modificar", result.stdout)

    def test_list_add_remove_flow(self):
        """Probar el flujo de añadir y quitar elementos de una lista (EXCLUDE_FILES)."""
        # Añadir (confirmando con 'y')
        runner.invoke(app, ["add", "exclude_files", "*.tmp, *.bak"], input="y\n")

        values = self.pm.get_config_values("test_user")
        self.assertIn("*.tmp", values["EXCLUDE_FILES"])  # type: ignore
        self.assertIn("*.bak", values["EXCLUDE_FILES"])  # type: ignore

        # Quitar uno
        runner.invoke(app, ["remove", "exclude_files", "*.tmp"], input="y\n")

        values = self.pm.get_config_values("test_user")
        self.assertNotIn("*.tmp", values["EXCLUDE_FILES"])  # type: ignore
        self.assertIn("*.bak", values["EXCLUDE_FILES"])  # type: ignore

    @patch("totelegram.commands.config._try_resolve_and_store_chat")
    def test_chat_id_trigger_resolution(self, mock_resolve):
        """Verificar que al cambiar el CHAT_ID se intenta resolver el nombre."""
        runner.invoke(app, ["set", "CHAT_ID", "@nuevo_chat"])
        mock_resolve.assert_called_once()
