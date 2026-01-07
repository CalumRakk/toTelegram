import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import dotenv_values, set_key, unset_key

from totelegram.core.schemas import ProfileRegistry
from totelegram.core.setting import get_user_config_dir

APP_NAME = "toTelegram"
CONFIG_DIR = Path(get_user_config_dir(APP_NAME))
PROFILES_DIR = CONFIG_DIR / "profiles"
CONFIG_FILE = CONFIG_DIR / "config.json"

logger = logging.getLogger(__name__)


class ProfileManager:
    def __init__(self):
        self._ensure_structure()

    def _ensure_structure(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_FILE.exists():
            self._save_config(ProfileRegistry())

    def _load_config(self) -> ProfileRegistry:
        """Carga la configuración y la devuelve como objeto Pydantic validado."""
        if not CONFIG_FILE.exists():
            return ProfileRegistry()

        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            return ProfileRegistry(**data)
        except (json.JSONDecodeError, ValueError):
            return ProfileRegistry()

    def _save_config(self, config: ProfileRegistry):
        """Guarda el objeto Pydantic en disco."""
        with open(CONFIG_FILE, "w") as f:
            f.write(config.model_dump_json(indent=4))

    def create_profile(self, name: str, api_id, api_hash, chat_id) -> Path:
        """Crea un archivo .env físico y lo registra."""
        env_content = (
            f"API_ID={api_id}\n"
            f"API_HASH={api_hash}\n"
            f"CHAT_ID={chat_id}\n"
            f"SESSION_NAME={name}\n"
        )

        file_path = PROFILES_DIR / f"{name}.env"
        with open(file_path, "w") as f:
            f.write(env_content)

        config = self._load_config()
        config.profiles[name] = str(file_path)
        self._save_config(config)

        return file_path

    def set_active(self, name: str):
        config = self._load_config()
        if name not in config.profiles:
            raise ValueError(f"El perfil '{name}' no existe.")

        config.active = name
        self._save_config(config)

    def get_profile_path(self, name: Optional[str] = None) -> Path:
        config = self._load_config()

        target = name if name else config.active

        if not target:
            raise ValueError(
                "No hay ningún perfil activo ni especificado. Ejecuta 'totelegram init'."
            )

        if target not in config.profiles:
            raise ValueError(f"El perfil '{target}' no existe.")

        return Path(config.profiles[target])

    def list_profiles(self) -> ProfileRegistry:
        """Devuelve el objeto tipado en lugar de un dict."""
        return self._load_config()

    def profile_exists(self, name: str) -> bool:
        config = self._load_config()
        return name in config.profiles

    def get_profile_values(
        self, name: Optional[str] = None
    ) -> Dict[str, Optional[str]]:
        path = self.get_profile_path(name)
        return dotenv_values(path)

    def update_setting(self, key: str, value: str, name: Optional[str] = None):
        path = self.get_profile_path(name)
        success, _, _ = set_key(path, key, value, quote_mode="never")
        if not success:
            raise IOError(f"No se pudo escribir en el archivo {path}")

    def delete_setting(self, key: str, name: Optional[str] = None):
        path = self.get_profile_path(name)
        success, _ = unset_key(path, key)
        if not success:
            raise IOError(f"No se pudo eliminar la clave {key} en {path}")

    def get_name_active_profile(self) -> Optional[str]:
        config = self._load_config()
        return config.active

    def get_profiles_names(self) -> List[str]:
        config = self._load_config()
        return list(config.profiles.keys())
