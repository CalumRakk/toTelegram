import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from typer.testing import CliRunner

from totelegram.cli.commands.config import app
from totelegram.cli.state import CLIState
from totelegram.identity import Settings, SettingsManager
from totelegram.schemas import AccessLevel

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
                "upload_limit_rate_kbps": "1000",
                "api_id": "123456789",
                "api_hash": "123456789",
            },
        )

        self.state = CLIState(manager=manager, profile_name=self.profile_name)
        self.manager = manager

    def tearDown(self):
        try:
            self.tmp_dir.cleanup()
        except Exception:  # noqa: S110
            pass

    def test_adr_001_immutable_profile_credentials(self):
        """
        ADR-001: Perfil Inmutable.

        Las credenciales base (API_ID, API_HASH) y el nombre del perfil
        DEBEN ser inmutables (DEBUG_READONLY) para garantizar la integridad de la sesión.
        """
        required_internal_fields = {"API_ID", "API_HASH", "PROFILE_NAME"}

        api_id = Settings.get_info("API_ID")
        api_hash = Settings.get_info("API_HASH")
        profile_name = Settings.get_info("PROFILE_NAME")

        error_message = (
            f"\n\n[VIOLACIÓN DE ARQUITECTURA - ADR-001]\n"
            f"Se intentó hacer editables campos críticos: {required_internal_fields}\n"
            f"Estos campos deben permanecer en Settings con nivel DEBUG_READONLY.\n"
        )

        self.assertTrue(
            api_id is not None and api_id.level == AccessLevel.DEBUG_READONLY,
            error_message,
        )
        self.assertTrue(
            api_hash is not None and api_hash.level == AccessLevel.DEBUG_READONLY,
            error_message,
        )
        self.assertTrue(
            profile_name is not None
            and profile_name.level == AccessLevel.DEBUG_READONLY,
            error_message,
        )

    def test_config_display_table(self):
        """Verificar que el comando base muestra la tabla de configuración."""
        result = runner.invoke(app, obj=self.state)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Configuración", result.stdout)
        self.assertIn("test_user", result.stdout)
        self.assertIn("upload_limit_rate_kbps", result.stdout)

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
        """Si se introduce un valor no numérico, Pydantic debe fallar."""
        result = runner.invoke(
            app, ["set", "MAX_FILESIZE_BYTES", "no_soy_un_numero"], obj=self.state
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("debe ser de tipo", result.stdout)

        settings = self.manager.get_settings(self.profile_name)
        current_value = getattr(settings, "MAX_FILESIZE_BYTES", None)
        self.assertNotEqual(current_value, "no_soy_un_numero")

    def test_set_internal_field_protection(self):
        """ADR-001: API_ID y otros campos internos son inmutables tras la creación."""
        result = runner.invoke(app, ["set", "api_id", "99999"], obj=self.state)
        self.assertIn("Solo Lectura", result.stdout.replace("\n", ""))

    def test_list_add(self):
        """Probar añadir elementos a una lista."""
        runner.invoke(app, ["add", "exclude_files", "*.tmp", "*.bak"], obj=self.state)

        settings = self.manager.get_settings(self.profile_name)
        self.assertIn("*.tmp", settings.exclude_files)
        self.assertIn("*.bak", settings.exclude_files)

    def test_list_add_remove_flow(self):
        """Probar el flujo de añadir y quitar elementos de una lista (exclude_files)."""
        runner.invoke(
            app, ["add", "exclude_files", "*.tmp", "*.bak"], input="y\n", obj=self.state
        )

        settings = self.manager.get_settings(self.profile_name)
        self.assertIn("*.tmp", settings.exclude_files)  # type: ignore
        self.assertIn("*.bak", settings.exclude_files)  # type: ignore

        runner.invoke(
            app, ["remove", "exclude_files", "*.tmp"], input="y\n", obj=self.state
        )

        settings = self.manager.get_settings(self.profile_name)
        self.assertNotIn("*.tmp", settings.exclude_files)  # type: ignore
        self.assertIn("*.bak", settings.exclude_files)  # type: ignore


if __name__ == "__main__":
    unittest.main()
