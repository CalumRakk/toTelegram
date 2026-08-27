from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, cast

from totelegram.database import DatabaseSession, normalize_database_url
from totelegram.telegram.client import TelegramSession

if TYPE_CHECKING:
    from totelegram.identity import SettingsManager


@dataclass
class CLIState:
    manager: "SettingsManager"
    profile_name: Optional[str]
    is_debug: bool = False

    def get_telegram_session(self, profile_name: str) -> TelegramSession:
        settings = self.manager.get_settings(profile_name)
        return TelegramSession(
            session_name=profile_name,
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            profiles_dir=self.manager.profiles_dir,
        )

    @contextmanager
    def scope(self):
        """Unifica el ciclo de vida de la DB y la Sesión resolviendo la URL del perfil."""
        profile_name = cast(str, self.manager.resolve_profile_name(self.profile_name))
        settings = self.manager.get_settings(profile_name)

        # Resolver la URL del backend activo
        db_url = normalize_database_url(
            settings.database_url, self.manager.database_path
        )

        # auto_init_schema=True asegura que las tablas existan antes de operar
        with DatabaseSession(db_url, auto_init_schema=True) as db:
            with self.get_telegram_session(profile_name) as client:
                yield client, db
