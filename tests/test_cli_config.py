import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from typer.testing import CliRunner

from totelegram.commands.config import app
from totelegram.core.registry import SettingsManager
from totelegram.core.schemas import CLIState

runner = CliRunner()


class TestCliConfig(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = TemporaryDirectory()
        self.config_path = Path(self.tmp_dir.name)

        self.profile_name = "test_user"
        manager = SettingsManager(self.config_path)
        manager._write_all_settings(
            "test_user",
            {
                "profile_name": self.profile_name,
                "chat_id": "123456789",
                "upload_limit_rate_kbps": "1000",
                "api_id": "123456789",
                "api_hash": "123456789",
            },
        )

        self.state = CLIState(manager=manager, profile_name=self.profile_name)
        self.manager = manager

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_config_display_table(self):
        """Verificar que el comando base muestra la tabla de configuración."""
        result = runner.invoke(app, obj=self.state)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Configuración", result.stdout)
        self.assertIn("test_user", result.stdout)
        self.assertIn("chat_id", result.stdout)

    def test_set_valid_value(self):
        """Probar que un cambio válido se persiste correctamente."""

        result = runner.invoke(
            app,
            ["set", "upload_limit_rate_kbps", "500"],
            obj=self.state,
        )

        self.assertEqual(result.exit_code, 0)
        settings = self.manager.get_settings(self.profile_name)
        current_value = getattr(settings, "upload_limit_rate_kbps", None)
        self.assertEqual(current_value, 500)

    def test_set_invalid_type_safety(self):
        """
        Si meto basura en un campo numérico,
        Pydantic debe saltar y el comando debe fallar.
        """

        result = runner.invoke(
            app, ["set", "MAX_FILESIZE_BYTES", "no_soy_un_numero"], obj=self.state
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("debe ser de tipo", result.stdout)

        # El valor original no debería haber cambiado
        settings = self.manager.get_settings(self.profile_name)
        current_value = getattr(settings, "MAX_FILESIZE_BYTES", None)
        self.assertNotEqual(current_value, "no_soy_un_numero")

    def test_set_internal_field_protection(self):
        """
        ADR-001: API_ID y otros campos internos son inmutables tras la creación.
        """
        result = runner.invoke(app, ["set", "api_id", "99999"], obj=self.state)

        self.assertIn("Solo Lectura", result.stdout.replace("\n", ""))

    def test_list_add(self):
        """Proba añadir elementos de una lista."""
        # Añadir (confirmando con 'y')
        runner.invoke(app, ["add", "exclude_files", "*.tmp", "*.bak"], obj=self.state)

        settings = self.manager.get_settings(self.profile_name)
        self.assertIn("*.tmp", settings.exclude_files)
        self.assertIn("*.bak", settings.exclude_files)

    # def test_list_add_remove_flow(self):
    #     """Probar el flujo de añadir y quitar elementos de una lista (exclude_files)."""
    #     # Añadir (confirmando con 'y')
    #     runner.invoke(
    #         app, ["add", "exclude_files", "*.tmp", "*.bak"], input="y\n", obj=self.pm
    #     )

    #     values = self.pm.get_config_values("test_user")
    #     self.assertIn("*.tmp", values["exclude_files"])  # type: ignore
    #     self.assertIn("*.bak", values["exclude_files"])  # type: ignore

    #     # Quitar uno
    #     runner.invoke(
    #         app, ["remove", "exclude_files", "*.tmp"], input="y\n", obj=self.pm
    #     )

    #     values = self.pm.get_config_values("test_user")
    #     self.assertNotIn("*.tmp", values["exclude_files"])  # type: ignore
    #     self.assertIn("*.bak", values["exclude_files"])  # type: ignore

    # @patch("totelegram.commands.config.resolve_and_store_chat_logic")
    # def test_chat_id_trigger_resolution(self, mock_resolve):
    #     """Verificar que al cambiar el CHAT_ID se intenta resolver el nombre."""
    #     runner.invoke(app, ["set", "CHAT_ID", "@nuevo_chat"], obj=self.state)
    #     mock_resolve.assert_called_once()
