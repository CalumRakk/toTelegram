import gettext
import locale
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

LanguageCode = Literal["es", "en"]
LanguageSetting = Literal["auto", "es", "en"]

LOCALES_DIR = Path(__file__).parent / "locales"
DOMAIN = "totelegram"

_current_language: LanguageCode = "es"
_current_translation: gettext.NullTranslations = gettext.NullTranslations()
_is_overridden: bool = False


def detect_system_language() -> LanguageCode:
    """Detecta el idioma del sistema operativo.

    Evalúa en orden de precedencia las variables de entorno estándar de la terminal
    (LC_ALL, LC_MESSAGES, LANG) y el locale del sistema.
    Si detecta español retorna 'es', de lo contrario 'en'.
    """
    candidates: list[str] = []

    # 1. Variables de entorno POSIX / Terminal
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.getenv(var)
        if val:
            candidates.append(val)

    # 2. Configuración regional del sistema operativo
    try:
        sys_loc, _ = locale.getdefaultlocale()
        if sys_loc:
            candidates.append(sys_loc)
    except Exception:  # noqa: S110
        pass

    for loc in candidates:
        clean = loc.lower().strip()
        if clean.startswith("es"):
            return "es"

    return "en"


def setup_i18n(
    lang: LanguageSetting | str | None = None, force: bool = False
) -> LanguageCode:
    """Inicializa y conmuta el catálogo de traducción activo en el runtime."""
    global _current_language, _current_translation

    # Si hay un override activo (ej. en tests), no permitir que setup_i18n("auto") lo sobreescriba
    if _is_overridden and not force:
        return _current_language

    resolved_lang = detect_system_language() if not lang or lang == "auto" else lang

    # Si es español, no cargamos catálogo: el código fuente YA es español
    if resolved_lang == "es":
        _current_translation = gettext.NullTranslations()
        _current_language = "es"
        return "es"

    # Si es inglés, cargamos el catálogo compilado .mo
    try:
        _current_translation = gettext.translation(
            domain=DOMAIN,
            localedir=str(LOCALES_DIR),
            languages=["en"],
            fallback=True,
        )
        _current_language = "en"
    except Exception:
        _current_translation = gettext.NullTranslations()
        _current_language = "es"

    return _current_language


def get_current_language() -> LanguageCode:
    """Devuelve el código del idioma activo ('es' o 'en')."""
    return _current_language


def _(message: str) -> str:
    """Traduce un mensaje según el catálogo activo."""
    return _current_translation.gettext(message)


def _n(singular: str, plural: str, n: int) -> str:
    """Traduce un mensaje con pluralización matemática según el catálogo activo."""
    return _current_translation.ngettext(singular, plural, n)


@contextmanager
def override_locale(lang: LanguageCode) -> Generator[LanguageCode, None, None]:
    """Context manager para forzar temporalmente un idioma (aislamiento en tests)."""
    global _is_overridden
    previous_lang = _current_language
    previous_overridden = _is_overridden
    _is_overridden = True
    try:
        setup_i18n(lang, force=True)
        yield lang
    finally:
        _is_overridden = previous_overridden
        setup_i18n(previous_lang, force=True)


# Inicialización estática defensiva en la importación
setup_i18n("auto")
