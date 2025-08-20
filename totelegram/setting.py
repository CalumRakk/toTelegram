import os
import sys
from os import getenv
from pathlib import Path
from platform import system
from typing import List, Optional, cast

from pydantic_settings import BaseSettings


def get_user_config_dir(app_name: str) -> Path:
    if sys.platform.startswith("win"):
        # En Windows se usa APPDATA → Roaming
        return Path(cast(str, getenv("APPDATA"))) / app_name
    elif sys.platform == "darwin":
        # En macOS
        return Path.home() / "Library" / "Application Support" / app_name
    else:
        # En Linux / Unix
        return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / app_name


class Settings(BaseSettings):
    session_name: str
    api_id: int
    api_hash: str
    chat_id: str

    worktable: Path = Path(get_user_config_dir("toTelegram"))
    exclude_files: List[str] = []

    min_filesize_bytes: int = 524_288_000
    max_filesize_bytes: int = 2_097_152_000
    max_filename_length: int = 55

    def __init__(self):
        super().__init__()
        self.worktable.mkdir(exist_ok=True, parents=True)

    def is_excluded(self, path: Path) -> bool:
        """Devuelve True si el archivo coincide con algún patrón de exclusión."""
        return any(path.match(pattern) for pattern in self.exclude_files)
