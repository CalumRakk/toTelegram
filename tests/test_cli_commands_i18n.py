import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from typer.testing import CliRunner

from totelegram.cli.__main__ import app
from totelegram.i18n import override_locale
from totelegram.identity import SettingsManager

runner = CliRunner()


class TestCliCommandsI18n(unittest.TestCase):
    """Pruebas unitarias de internacionalización para la Capa 4 (Comandos y Prompts)."""

    def setUp(self):
        self.tmp_dir = TemporaryDirectory()
        self.worktable = Path(self.tmp_dir.name)
        self.manager = SettingsManager(self.worktable)

        # Aislar de la configuración real del usuario en ~/.config/toTelegram
        self.patcher_cfg = patch(
            "totelegram.cli.__main__.get_user_config_dir",
            return_value=self.worktable,
        )
        self.patcher_cfg.start()

    def tearDown(self):
        self.patcher_cfg.stop()
        self.tmp_dir.cleanup()

    def test_version_output_clean(self):
        """Verifica que el callback de versión funcione en ambos idiomas."""
        for lang in ("es", "en"):
            with override_locale(lang):
                res = runner.invoke(app, ["--version"])
                self.assertEqual(res.exit_code, 0)
                self.assertIn("toTelegram", res.stdout)

    def test_auth_status_unconfigured_prompt_bilingual(self):
        """Valida el mensaje de advertencia cuando no hay cuenta activa."""
        with override_locale("es"):
            res_es = runner.invoke(app, ["status"])
            self.assertEqual(res_es.exit_code, 0)
            self.assertIn("No hay una cuenta activa configurada", res_es.stdout)

        with override_locale("en"):
            res_en = runner.invoke(app, ["status"])
            self.assertEqual(res_en.exit_code, 0)
            self.assertIn("No active account configured", res_en.stdout)

    def test_switch_no_accounts_bilingual(self):
        """Valida el mensaje de advertencia de switch cuando no hay cuentas registradas."""
        with override_locale("es"):
            res_es = runner.invoke(app, ["switch"])
            self.assertEqual(res_es.exit_code, 0)
            self.assertIn("No hay cuentas guardadas en el sistema", res_es.stdout)

        with override_locale("en"):
            res_en = runner.invoke(app, ["switch"])
            self.assertEqual(res_en.exit_code, 0)
            self.assertIn("No saved accounts in the system", res_en.stdout)

    def test_prune_unbacked_folder_bilingual(self):
        """Valida la respuesta del comando prune en carpetas sin archivos respaldados."""
        empty_folder = self.worktable / "vacia"
        empty_folder.mkdir()

        with patch(
            "totelegram.cli.commands.prune.LocalAuditService.audit_path",
            return_value=[],
        ):
            with override_locale("es"):
                res_es = runner.invoke(app, ["prune", str(empty_folder)])
                self.assertEqual(res_es.exit_code, 0)
                self.assertIn(
                    "No hay archivos respaldados que puedan liberarse", res_es.stdout
                )

            with override_locale("en"):
                res_en = runner.invoke(app, ["prune", str(empty_folder)])
                self.assertEqual(res_en.exit_code, 0)
                self.assertIn("No backed up files can be safely freed", res_en.stdout)

    def test_config_check_profile_not_found_bilingual(self):
        """Valida mensajes de check_config cuando el perfil no existe."""
        with override_locale("es"):
            res_es = runner.invoke(app, ["config", "check"])
            self.assertEqual(res_es.exit_code, 1)
            self.assertIn("No hay un perfil activo", res_es.stdout)

        with override_locale("en"):
            res_en = runner.invoke(app, ["config", "check"])
            self.assertEqual(res_en.exit_code, 1)
            self.assertIn("There is no active profile", res_en.stdout)


if __name__ == "__main__":
    unittest.main()
