import os
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from totelegram.i18n import (
    LOCALES_DIR,
    _,
    _n,
    detect_system_language,
    get_current_language,
    override_locale,
    setup_i18n,
)
from totelegram.identity import Settings


class TestI18nCore(unittest.TestCase):
    """Pruebas unitarias para el motor central de internacionalización (Capa 1)."""

    def test_compiled_mo_files_exist(self):
        """Verifica que el archivo binario compilado .mo exista para inglés."""
        en_mo = LOCALES_DIR / "en" / "LC_MESSAGES" / "totelegram.mo"

        # Si no existe, compilar automáticamente para que el desarrollador no sufra
        if not en_mo.exists():
            import subprocess

            subprocess.run(
                [
                    "python",
                    "-m",
                    "babel.messages.frontend",
                    "compile",
                    "-d",
                    str(LOCALES_DIR),
                    "-D",
                    "totelegram",
                ],
                check=True,
            )

        self.assertTrue(
            en_mo.exists(),
            "Falta compilar totelegram/locales/en/LC_MESSAGES/totelegram.mo",
        )
        self.assertGreater(en_mo.stat().st_size, 0)

    def test_detect_system_language_spanish(self):
        """Comprueba que entornos configurados con español resuelvan 'es'."""
        with patch.dict(os.environ, {"LANG": "es_ES.UTF-8", "LC_ALL": ""}):
            self.assertEqual(detect_system_language(), "es")

        with patch.dict(os.environ, {"LANG": "es_MX.UTF-8", "LC_ALL": ""}):
            self.assertEqual(detect_system_language(), "es")

    def test_detect_system_language_english_and_others(self):
        """Comprueba que entornos no-español resuelvan 'en' por defecto."""
        with patch.dict(os.environ, {"LANG": "en_US.UTF-8", "LC_ALL": ""}):
            self.assertEqual(detect_system_language(), "en")

        with patch.dict(os.environ, {"LANG": "fr_FR.UTF-8", "LC_ALL": ""}):
            self.assertEqual(detect_system_language(), "en")

        with patch.dict(os.environ, {}, clear=True):
            with patch("locale.getdefaultlocale", return_value=(None, None)):
                self.assertEqual(detect_system_language(), "en")

    def test_setup_i18n_switching(self):
        """Verifica la conmutación entre catálogos y cadenas traducidas."""
        setup_i18n("es")
        self.assertEqual(get_current_language(), "es")
        self.assertEqual(_("Hola Mundo"), "Hola Mundo")

        setup_i18n("en")
        self.assertEqual(get_current_language(), "en")
        self.assertEqual(_("Hola Mundo"), "Hello World")

    def test_pluralization(self):
        """Verifica las reglas gramaticales de pluralización con _n()."""
        with override_locale("es"):
            self.assertEqual(
                _n("{n} archivo", "{n} archivos", 1).format(n=1), "1 archivo"
            )
            self.assertEqual(
                _n("{n} archivo", "{n} archivos", 5).format(n=5), "5 archivos"
            )

        with override_locale("en"):
            self.assertEqual(_n("{n} archivo", "{n} archivos", 1).format(n=1), "1 file")
            self.assertEqual(
                _n("{n} archivo", "{n} archivos", 5).format(n=5), "5 files"
            )

    def test_override_locale_context_manager(self):
        """Comprueba que override_locale restaure el idioma original al salir."""
        setup_i18n("es")
        self.assertEqual(get_current_language(), "es")

        with override_locale("en"):
            self.assertEqual(get_current_language(), "en")
            self.assertEqual(_("Hola Mundo"), "Hello World")

        self.assertEqual(get_current_language(), "es")
        self.assertEqual(_("Hola Mundo"), "Hola Mundo")

    def test_settings_language_validation(self):
        """Valida que Settings solo admita 'auto', 'es' o 'en'."""
        s_default = Settings(profile_name="test")
        self.assertEqual(s_default.language, "auto")

        s_es = Settings(profile_name="test", language="es")
        self.assertEqual(s_es.language, "es")

        s_en = Settings(profile_name="test", language="en")
        self.assertEqual(s_en.language, "en")

        with self.assertRaises(ValidationError):
            Settings(profile_name="test", language="fr")  # Idioma no admitido


if __name__ == "__main__":
    unittest.main()
