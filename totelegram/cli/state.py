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

    @contextmanager
    def scope(self):
        """Unifica el ciclo de vida de la DB y la Sesión."""
        profile_name = cast(str, self.manager.resolve_profile_name(self.profile_name))

        with DatabaseSession(self.manager.database_path) as db:
            with TelegramSession.from_profile(profile_name, self.manager) as client:
                yield client, db
