import json
import logging
from pathlib import Path
from typing import Dict, Optional

from dotenv import dotenv_values, set_key, unset_key

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
            self._save_config({"active": None, "profiles": {}})

    def _load_config(self) -> dict:
        if not CONFIG_FILE.exists():
            return {"active": None, "profiles": {}}

        with open(CONFIG_FILE, "r") as f:
            return json.load(f)

    def _save_config(self, data: dict):
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=4)

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
        config["profiles"][name] = str(file_path)

        self._save_config(config)
        return file_path

    def set_active(self, name: str):
        config = self._load_config()
        if name not in config["profiles"]:
            raise ValueError(f"El perfil '{name}' no existe.")
        config["active"] = name
        self._save_config(config)

    def get_profile_path(self, name: Optional[str] = None) -> Path:
        """Devuelve la ruta del .env. Si name es None, usa el activo."""
        config = self._load_config()

        if name:
            target = name
        else:
            target = config["active"]

        if not target:
            raise ValueError(
                "No hay ningún perfil activo ni especificado. Ejecuta 'totelegram init'."
            )

        if target not in config["profiles"]:
            raise ValueError(f"El perfil '{target}' no existe.")

        return Path(config["profiles"][target])

    def list_profiles(self) -> Dict[str, str]:
        config = self._load_config()
        return {"active": config["active"], "profiles": config["profiles"]}

    def profile_exists(self, name: str) -> bool:
        config = self._load_config()
        return name in config["profiles"]

    def get_profile_values(
        self, name: Optional[str] = None
    ) -> Dict[str, Optional[str]]:
        """Devuelve un diccionario con las claves y valores del .env del perfil."""
        path = self.get_profile_path(name)
        return dotenv_values(path)

    def update_setting(self, key: str, value: str, name: Optional[str] = None):
        """Actualiza o añade una clave en el .env del perfil especificado."""
        path = self.get_profile_path(name)

        # set_key escribe físicamente en el archivo .env manteniendo el formato
        success, _, _ = set_key(path, key, value, quote_mode="never")
        if not success:
            raise IOError(f"No se pudo escribir en el archivo {path}")

    def delete_setting(self, key: str, name: Optional[str] = None):
        """Elimina (comenta/borra) una clave del .env."""
        path = self.get_profile_path(name)
        success, _ = unset_key(path, key)
        if not success:
            raise IOError(f"No se pudo eliminar la clave {key} en {path}")

    def get_name_active_profile(self) -> Optional[str]:
        """Devuelve el nombre del perfil activo."""
        config = self._load_config()
        return config.get("active")

    def get_profiles_names(self) -> list[str]:
        config = self._load_config()
        return list(config["profiles"].keys())
