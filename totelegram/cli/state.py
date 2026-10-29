from contextlib import contextmanager
from typing import TYPE_CHECKING

from totelegram.database import DatabaseSession
from totelegram.telegram.client import TelegramSession

if TYPE_CHECKING:
    from totelegram.identity import Settings, SettingsManager


class CLIState:
    """Estado contextual activo para la sesión de comandos CLI."""

    def __init__(
        self,
        manager: "SettingsManager",
        profile_name: str | None = None,
        is_debug: bool = False,
    ):
        self.manager = manager
        self.raw_profile_name = profile_name
        self.is_debug = is_debug

        self._resolved_profile_name: str | None = None
        self._settings: "Settings | None" = None

    def get_profile_name(self, strict: bool = True) -> str | None:
        """
        Resuelve y cachea el nombre del perfil activo.
        Si strict=True y no hay perfil, lanza ValueError.
        """
        if self._resolved_profile_name is None:
            resolved = self.manager.resolve_profile_name(
                self.raw_profile_name, strict=strict
            )
            if resolved is not None:
                self._resolved_profile_name = resolved
            return resolved
        return self._resolved_profile_name

    @property
    def profile_name(self) -> str:
        """
        Nombre garantizado del perfil activo en modo estricto.
        Lanza ValueError descriptivo si no existe.
        """
        name = self.get_profile_name(strict=True)
        assert name is not None
        return name

    @property
    def settings(self) -> "Settings":
        """Configuración del perfil activo (con caché perezoso)."""
        if self._settings is None:
            self._settings = self.manager.get_settings(self.profile_name)
        return self._settings

    def announce_profile(self) -> None:
        """Emite el aviso visual del perfil utilizado a través de la UI."""
        from totelegram.cli.views.settings import DisplayProfile

        DisplayProfile.announce_profile_used(self.profile_name)

    def get_telegram_session(self, profile_name: str | None = None) -> TelegramSession:
        """Crea una sesión de cliente Telegram para el perfil indicado o activo."""
        target_name = profile_name or self.profile_name
        settings = self.manager.get_settings(target_name)
        return TelegramSession(
            session_name=target_name,
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            profiles_dir=self.manager.profiles_dir,
        )

    @contextmanager
    def scope(self):
        """Unifica el ciclo de vida de la base de datos local SQLite y la sesión de Telegram."""
        with (
            DatabaseSession(self.manager.database_path, auto_init_schema=True) as db,
            self.get_telegram_session(self.profile_name) as client,
        ):
            yield client, db
