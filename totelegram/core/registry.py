import glob
import json
import logging
from pathlib import Path
from typing import Dict, List, Literal, Optional, get_origin

from dotenv import dotenv_values, set_key

from totelegram.core.schemas import ProfileRegistry
from totelegram.core.setting import Settings, get_user_config_dir

APP_SESSION_NAME = "toTelegram"
CONFIG_DIR = Path(get_user_config_dir(APP_SESSION_NAME))
logger = logging.getLogger(__name__)


class ProfileManager:
    """
    Gestiona el ciclo de vida de los perfiles (creación, activación, rutas)
    y la persistencia de sus configuraciones.
    """

    _global_override: Optional[str] = None

    PROFILES_DIR = CONFIG_DIR / "profiles"
    CONFIG_FILE = CONFIG_DIR / "config.json"

    def __init__(self):
        self._ensure_structure()

    def create(self, name: str, api_id: int, api_hash: str, chat_id: str) -> Path:
        """Crea un nuevo perfil físico y lo registra."""
        env_content = (
            f"API_ID={api_id}\n"
            f"API_HASH={api_hash}\n"
            f"CHAT_ID={chat_id}\n"
            f"PROFILE_NAME={name}\n"
        )

        file_path = self.PROFILES_DIR / f"{name}.env"
        with open(file_path, "w") as f:
            f.write(env_content)

        registry = self._load_registry()
        registry.profiles[name] = str(file_path)
        self._save_registry(registry)

        return file_path

    def resolve_name(self, override_name: Optional[str] = None) -> str:
        """
        Resuelve el nombre del perfil con una jerarquía estricta:
        1. Si se pasa override_name por parámetro (prioridad absoluta del programador).
        2. Si se seteó el flag global --use en el CLI (_global_override).
        3. Si existe un perfil activo en el registro (config.json).

        Si ninguna se cumple o el perfil resultante no existe, EXPLOTA.
        """
        # Intentamos obtener el nombre por jerarquía
        target = override_name or self._global_override or self.active_name

        if not target:
            raise ValueError(
                "Operación fallida: No se especificó un perfil con '--use' "
                "ni existe un perfil activo globalmente. "
                "Usa 'totelegram profile switch <nombre>' para activar uno."
            )

        # Validación de existencia (Strict)
        if not self.exists(target):
            raise ValueError(f"El perfil '{target}' no existe en el sistema.")

        return target

    def activate(self, name: str):
        """Establece un perfil como activo."""
        registry = self._load_registry()
        if name not in registry.profiles:
            raise ValueError(f"El perfil '{name}' no existe.")

        registry.active = name
        self._save_registry(registry)

    def get_registry(self) -> ProfileRegistry:
        """Devuelve el registro completo, sincronizando archivos huérfanos si es necesario."""
        return self._sync_registry_with_filesystem()

    def exists(self, name: str) -> bool:
        """Verifica si un perfil existe en el registro."""
        registry = self._load_registry()
        return name in registry.profiles

    @property
    def active_name(self) -> Optional[str]:
        """Devuelve el nombre del perfil activo (sin sincronizar disco innecesariamente)."""
        return self._load_registry().active

    def get_path(self, name: Optional[str] = None) -> Path:
        """
        Resuelve la ruta del archivo .env.
        Si name es None, usa el activo.
        """
        registry = self.get_registry()
        target = name or registry.active

        if not target:
            raise ValueError("No hay perfil activo ni especificado.")

        if target not in registry.profiles:
            raise ValueError(f"El perfil '{target}' no se encuentra en el registro.")

        return Path(registry.profiles[target])

    def get_config_values(
        self, profile_name: Optional[str] = None
    ) -> Dict[str, Optional[str]]:
        """Devuelve el contenido raw del .env."""
        path = self.get_path(profile_name)
        return dotenv_values(path)

    def update_config(self, key: str, value: str, profile_name: Optional[str] = None):
        """
        Valida y actualiza una configuración simple.
        Maneja la conversión de tipos vía Pydantic (Settings).
        """
        key = key.upper()
        self._validate_key_is_modifiable(key)

        field_info = Settings.model_fields[key.lower()]
        origin = get_origin(field_info.annotation)

        if (origin is list or origin is List) and value.startswith("["):

            try:
                json_val = json.loads(value)
                validated_val = Settings.validate_single_setting(key, json_val)
                value_to_save = json.dumps(validated_val)
            except json.JSONDecodeError:
                raise ValueError(f"El valor para {key} debe ser un JSON válido.")
        else:

            validated_val = Settings.validate_single_setting(key, value)
            value_to_save = str(validated_val)

        self._write_to_env(key, value_to_save, profile_name)
        return validated_val

    def update_config_list(
        self,
        action: Literal["add", "remove"],
        key: str,
        values: List[str],
        profile_name: Optional[str] = None,
    ):
        """Gestiona la adición/remoción de elementos en configuraciones tipo lista."""
        key = key.upper()
        self._validate_key_is_modifiable(key)

        current_list = self._parse_env_list(key, profile_name)

        if action == "add":
            for val in values:
                if val not in current_list:
                    current_list.append(val)
        elif action == "remove":
            for val in values:
                if val in current_list:
                    current_list.remove(val)

        Settings.validate_single_setting(key, current_list)  # type: ignore

        self._write_to_env(key, json.dumps(current_list), profile_name)
        return current_list

    def _ensure_structure(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        if not self.CONFIG_FILE.exists():
            self._save_registry(ProfileRegistry())

    def _load_registry(self) -> ProfileRegistry:
        """Carga el JSON raw sin lógica extra."""
        if not self.CONFIG_FILE.exists():
            return ProfileRegistry()
        try:
            with open(self.CONFIG_FILE, "r") as f:
                return ProfileRegistry(**json.load(f))
        except (json.JSONDecodeError, ValueError):
            return ProfileRegistry()

    def _save_registry(self, registry: ProfileRegistry):
        with open(self.CONFIG_FILE, "w") as f:
            f.write(registry.model_dump_json(indent=4))

    def _sync_registry_with_filesystem(self) -> ProfileRegistry:
        """
        Lógica de 'Sanidad': Verifica que los archivos listados en config.json existan,
        y busca archivos .env nuevos que no estén registrados.
        """
        registry = self._load_registry()
        dirty = False
        valid_profiles = {}

        for name, path_str in registry.profiles.items():
            if Path(path_str).exists():
                valid_profiles[name] = path_str
            else:
                logger.warning(f"Perfil roto eliminado del registro: '{name}'")
                dirty = True

        existing_env_files = glob.glob(str(self.PROFILES_DIR / "*.env"))
        for env_path in existing_env_files:
            p_name = Path(env_path).stem
            if p_name not in valid_profiles:
                valid_profiles[p_name] = str(env_path)
                dirty = True
                logger.info(f"Perfil recuperado: '{p_name}'")

        if dirty:
            registry.profiles = valid_profiles
            if registry.active and registry.active not in valid_profiles:
                registry.active = None
            self._save_registry(registry)

        return registry

    def _validate_key_is_modifiable(self, key: str):
        if key in Settings.INTERNAL_FIELDS:
            raise ValueError(f"La clave '{key}' es interna y no se puede modificar.")
        if key.lower() not in Settings.model_fields:
            raise ValueError(f"La configuración '{key}' no es válida.")

    def _write_to_env(self, key: str, value: str, profile_name: Optional[str]):
        path = self.get_path(profile_name)
        success, _, _ = set_key(path, key, value, quote_mode="never")
        if not success:
            raise IOError(f"No se pudo escribir en {path}")

    def _parse_env_list(self, key: str, profile_name: Optional[str]) -> List[str]:
        raw = self.get_config_values(profile_name).get(key, "")
        if not raw:
            return []
        try:
            val = json.loads(raw)
            return val if isinstance(val, list) else [str(val)]
        except json.JSONDecodeError:
            return [x.strip() for x in raw.split(",") if x.strip()]

    def delete_profile(self, name: str):
        """Elimina un perfil del registro y borra su archivo físico."""
        registry = self._load_registry()
        if name not in registry.profiles:
            raise ValueError(f"El perfil '{name}' no existe.")

        path_str = registry.profiles.pop(name)
        self._save_registry(registry)

        path = Path(path_str)
        if path.exists():
            path.unlink()

        session_path = self.PROFILES_DIR / f"{name}.session"
        if session_path.exists():
            session_path.unlink()
