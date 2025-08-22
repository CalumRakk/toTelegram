import os
import sys
from os import getenv
from pathlib import Path
from platform import system
from typing import List, Optional, Union, cast

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    api_hash: str = Field(..., description="Telegram API hash")
    api_id: int = Field(..., description="Telegram API ID")
    session_name: str = "me"
    chat_id: Union[str, int] = Field(
        ..., description="ID del chat o enlace de invitación"
    )

    app_name: str = "toTelegram"
    worktable: Path = Path(get_user_config_dir(app_name)).resolve()
    exclude_files: List[str] = []
    database_name: str = f"{app_name}.sqlite"

    max_filesize_bytes: int = 2_097_152_000
    max_filename_length: int = 55

    @property
    def database_path(self) -> Path:
        return self.worktable / self.database_name

    def is_excluded(self, path: Path) -> bool:
        """Devuelve True si el archivo coincide con algún patrón de exclusión."""
        return any(path.match(pattern) for pattern in self.exclude_files)

    @field_validator("chat_id", mode="before")
    def convert_to_int_if_possible(cls, v):
        try:
            return int(v)
        except (ValueError, TypeError):
            return str(v)


def get_settings(env_path: Union[Path, str] = ".env") -> Settings:
    """
    Carga configuración desde un archivo .env

    La utilidad principal es ocultar el falso positivo de pylance al advertir que faltan argumentos que van a ser cargados desde el archivo .env
    Ver https://github.com/pydantic/pydantic/issues/3753
    """
    env_path = Path(env_path) if isinstance(env_path, str) else env_path
    try:
        if env_path.exists():
            settings = Settings(_env_file=env_path)  # type: ignore
            return settings
        raise FileNotFoundError(f"El archivo de configuración {env_path} no existe.")
    except ValidationError as e:
        print("❌ Error en configuración:", e)
        raise
