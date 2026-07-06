from contextlib import contextmanager
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Optional,
    cast,
)

from totelegram.database import DatabaseSession
from totelegram.telegram.client import TelegramSession

if TYPE_CHECKING:
    from totelegram.identity import SettingsManager


@dataclass
class CLIState:
    manager: "SettingsManager"
    profile_name: Optional[str]
    is_debug: bool = False

    def get_telegram_session(self, profile_name: str) -> TelegramSession:
        """Instancia la sesión de Telegram usando tipos primitivos desde la configuración."""
        settings = self.manager.get_settings(profile_name)
        return TelegramSession(
            session_name=profile_name,
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            profiles_dir=self.manager.profiles_dir,
        )

    @contextmanager
    def scope(self):
        """Unifica el ciclo de vida de la DB y la Sesión."""
        profile_name = cast(str, self.manager.resolve_profile_name(self.profile_name))

        with DatabaseSession(self.manager.database_path) as db:
            with self.get_telegram_session(profile_name) as client:
                yield client, db
