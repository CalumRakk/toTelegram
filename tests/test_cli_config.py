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
        self.tmp_dir = TemporaryDirectory()
        self.config_path = Path(self.tmp_dir.name)
        self.pm = ProfileManager(base_dir=self.config_path)
        self.pm.create("test_user", 12345, "fake_hash", "123456789")
        self.pm.activate("test_user")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_config_display_table(self):
        """Verificar que el comando base muestra la tabla de configuración."""
        result = runner.invoke(app, obj=self.pm)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Configuración", result.stdout)
        self.assertIn("test_user", result.stdout)
        self.assertIn("CHAT_ID", result.stdout)

    def test_set_valid_value(self):
        """Probar que un cambio válido se persiste correctamente."""

        result = runner.invoke(
            app, ["set", "upload_limit_rate_kbps", "500"], obj=self.pm
        )

        self.assertEqual(result.exit_code, 0)
        values = self.pm.get_config_values("test_user")
        self.assertEqual(values["UPLOAD_LIMIT_RATE_KBPS"], "500")

    def test_set_invalid_type_safety(self):
        """
        Si meto basura en un campo numérico,
        Pydantic debe saltar y el comando debe fallar.
        """
        # MAX_FILESIZE_BYTES espera un entero
        result = runner.invoke(
            app, ["set", "MAX_FILESIZE_BYTES", "no_soy_un_numero"], obj=self.pm
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Error", result.stdout)

        # El valor original no debería haber cambiado
        values = self.pm.get_config_values("test_user")
        self.assertNotEqual(values.get("MAX_FILESIZE_BYTES"), "no_soy_un_numero")

    def test_set_internal_field_protection(self):
        """
        ADR-001: API_ID y otros campos internos son inmutables tras la creación.
        """
        result = runner.invoke(app, ["set", "API_ID", "99999"], obj=self.pm)

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("es interna y no se puede modificar", result.stdout)

    def test_list_add_remove_flow(self):
        """Probar el flujo de añadir y quitar elementos de una lista (EXCLUDE_FILES)."""
        # Añadir (confirmando con 'y')
        runner.invoke(
            app, ["add", "exclude_files", "*.tmp, *.bak"], input="y\n", obj=self.pm
        )

        values = self.pm.get_config_values("test_user")
        self.assertIn("*.tmp", values["EXCLUDE_FILES"])  # type: ignore
        self.assertIn("*.bak", values["EXCLUDE_FILES"])  # type: ignore

        # Quitar uno
        runner.invoke(
            app, ["remove", "exclude_files", "*.tmp"], input="y\n", obj=self.pm
        )

        values = self.pm.get_config_values("test_user")
        self.assertNotIn("*.tmp", values["EXCLUDE_FILES"])  # type: ignore
        self.assertIn("*.bak", values["EXCLUDE_FILES"])  # type: ignore

    @patch("totelegram.commands.config.resolve_and_store_chat_logic")
    def test_chat_id_trigger_resolution(self, mock_resolve):
        """Verificar que al cambiar el CHAT_ID se intenta resolver el nombre."""
        runner.invoke(app, ["set", "CHAT_ID", "@nuevo_chat"], obj=self.pm)
        mock_resolve.assert_called_once()
