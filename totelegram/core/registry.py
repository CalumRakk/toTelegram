import glob
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from dotenv import dotenv_values, set_key, unset_key

from totelegram.core.schemas import ProfileRegistry
from totelegram.core.setting import (
    AccesField,
    AccessLevel,
    Settings,
    get_user_config_dir,
)

APP_SESSION_NAME = "toTelegram"
CONFIG_DIR = Path(get_user_config_dir(APP_SESSION_NAME))
logger = logging.getLogger(__name__)


class ProfileManager:
    """
    Gestiona el ciclo de vida de los perfiles.
    Ahora las rutas son inyectadas o calculadas por instancia, no globales.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self.config_dir = base_dir or Path(get_user_config_dir(APP_SESSION_NAME))
        self.profiles_dir = self.config_dir / "profiles"
        self.config_file = self.config_dir / "config.json"
        self._override: Optional[str] = None

        self._is_debug = False
        self._ensure_structure()

    def set_override(self, name: str):
        """Establece un perfil como activo.
        Util si usamos una instancia de Profile de forma global.
        """
        self._override = name

    def _ensure_structure(self):
        """Crea las carpetas necesarias solo cuando se instancia."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_file.exists():
            self._save_registry(ProfileRegistry())

    def create(self, name: str, api_id: int, api_hash: str, chat_id: str) -> Path:
        """Crea un nuevo perfil físico y lo registra."""
        env_content = (
            f"API_ID={api_id}\n"
            f"API_HASH={api_hash}\n"
            f"CHAT_ID={chat_id}\n"
            f"PROFILE_NAME={name}\n"
        )

        file_path = self.profiles_dir / f"{name}.env"
        with open(file_path, "w") as f:
            f.write(env_content)

        registry = self._load_registry()
        registry.profiles[name] = str(file_path)
        self._save_registry(registry)
        return file_path

    def _load_registry(self) -> ProfileRegistry:
        """Carga el JSON raw sin lógica extra."""
        if not self.config_file.exists():
            return ProfileRegistry()
        try:
            with open(self.config_file, "r") as f:
                return ProfileRegistry(**json.load(f))
        except (json.JSONDecodeError, ValueError):
            return ProfileRegistry()

    def _save_registry(self, registry: ProfileRegistry):
        with open(self.config_file, "w") as f:
            f.write(registry.model_dump_json(indent=4))

    def resolve_name(self, override_name: Optional[str] = None) -> str:
        """
        Resuelve el nombre del perfil con una jerarquía estricta:
        1. Si se pasa override_name por parámetro (prioridad absoluta del programador).
        2. Si se seteó el flag global --use en el CLI (_global_override).
        3. Si existe un perfil activo en el registro (config.json).

        Si ninguna se cumple o el perfil resultante no existe, EXPLOTA.
        """
        # Intentamos obtener el nombre por jerarquía
        target = override_name or self._override or self.active_name

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

    @property
    def is_debug(self) -> bool:
        return self._is_debug

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
        """Devuelve el contenido raw del .env.

        Si una Key viene del .env en mayuscula, se minimiza.
        """
        path = self.get_path(profile_name)
        data = dotenv_values(path)
        return {k.lower(): v for k, v in data.items()}

    def get_settings(self, profile_name: Optional[str] = None) -> Settings:
        if profile_name is None:
            profile_name = self.active_name

        env_path = self.get_path(profile_name)
        settings = Settings(_env_file=env_path)  # type: ignore
        if self._is_debug:
            settings.database_name = "debug_inventory.sqlite"
        return settings

    def _validate_key_access(self, info: AccesField):
        """Valida si el nivel de acceso permite la edición en el contexto actual."""
        if info.level == AccessLevel.DEBUG_READONLY:
            raise ValueError(
                f"La configuracion '{info.field_name.upper()}' es de identidad (Solo Lectura)."
            )

        if info.level == AccessLevel.DEBUG_EDITABLE and not self._is_debug:
            raise ValueError(
                f"La configuracion '{info.field_name.upper()}' solo es modificable en modo --debug."
            )

    def update_config(self, key: str, value: Any, profile_name: Optional[str] = None):
        """
        Actualiza una configuracion de forma declarativa usando los metadatos de Settings.
        """
        key_lower = key.lower()
        info = Settings.get_info(key_lower)

        if not info:
            raise ValueError(f"La configuracion '{key.upper()}' no existe.")

        self._validate_key_access(info)

        # Si esperamos una lista y recibimos un string (ej. desde CLI), intentamos parsear
        processed_value = value
        if "list" in info.type_annotation.lower() and isinstance(value, str):
            if value.startswith("["):
                try:
                    processed_value = json.loads(value)
                except json.JSONDecodeError:
                    raise ValueError(
                        f"Formato JSON invalido para la lista '{key.upper()}'."
                    )
            else:
                # Soporte para valores separados por coma: "val1, val2"
                processed_value = [x.strip() for x in value.split(",") if x.strip()]

        validated_val = Settings.validate_single_setting(key_lower, processed_value)

        # Convertimos a formato apto para .env (JSON para listas, string para el resto)
        value_to_save = (
            json.dumps(validated_val)
            if isinstance(validated_val, list)
            else str(validated_val)
        )

        self._write_to_env(key.upper(), value_to_save, profile_name)
        return validated_val

    def update_config_list(
        self,
        action: Literal["add", "remove"],
        key: str,
        values: List[str],
        profile_name: Optional[str] = None,
    ):

        current_list = self._parse_env_list(key.upper(), profile_name)

        if action == "add":
            for val in values:
                if val not in current_list:
                    current_list.append(val)
        elif action == "remove":
            for val in values:
                if val in current_list:
                    current_list.remove(val)
        return self.update_config(key, current_list, profile_name)

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

        existing_env_files = glob.glob(str(self.config_dir / "*.env"))
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
        key = key.lower()
        if key not in Settings.model_fields:
            raise ValueError(f"La configuración '{key.upper()}' no es válida.")

        level = Settings.get_info(key)
        if level == AccessLevel.DEBUG_READONLY:
            raise ValueError(
                f"La clave '{key.upper()}' es de identidad y no puede modificarse."
            )

        if level == AccessLevel.DEBUG_EDITABLE and not self._is_debug:
            raise ValueError(
                f"La clave '{key.upper()}' es una constante del sistema. "
                "Para modificarla con fines de prueba, usa el modo --debug."
            )

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

        session_path = self.config_dir / f"{name}.session"
        if session_path.exists():
            session_path.unlink()

    def unset_config(
        self, key: str, profile_name: Optional[str] = None
    ) -> Tuple[bool, Any]:
        """
        Elimina una clave del archivo .env para que Pydantic use el valor por defecto.
        Retorna el valor por defecto que ha quedado activo.
        """
        key = key.upper()

        self._validate_key_is_modifiable(key)

        path = self.get_path(profile_name)

        # intenta eliminar la clave del archivo físico
        succes, _ = unset_key(path, key, quote_mode="never")
        if not succes:
            return False, None

        # Instanciamos Settings para averiguar cuál es el default
        field_info = Settings.model_fields.get(key.lower())

        if field_info:
            return True, field_info.default
        return True, None

    def fork_for_debug(self, source_name: str):

        debug_name = f"{source_name}_debug"
        debug_env_path = self.profiles_dir / f"{debug_name}.env"

        values = self.get_config_values(source_name)
        values["PROFILE_NAME"] = debug_name
        values["DATABASE_NAME"] = "debug_inventory.sqlite"
        values["CHAT_ID"] = "me"  # chat_id por defecto.

        with open(debug_env_path, "w") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")

        # Clona la session de producción
        source_session = self.profiles_dir / f"{source_name}.session"
        debug_session = self.profiles_dir / f"{debug_name}.session"
        if source_session.exists():
            shutil.copy2(source_session, debug_session)
        else:
            logger.warning(f"No se encontró session para '{source_name}'.")
            raise FileNotFoundError(f"No se encontró session para '{source_name}'")

        registry = self._load_registry()
        registry.profiles[debug_name] = str(debug_env_path)
        self._save_registry(registry)
        return debug_name

    def _set_debug(self):
        self._is_debug = True

    def setup_debug_context(self, base_profile_name: Optional[str] = None):
        """
        Asegura que exista un perfil shadow y lo activa para la sesión actual.
        """
        base = base_profile_name or self.active_name
        if not base:
            raise ValueError(
                "No se puede iniciar modo debug sin un perfil base activo o especificado."
            )

        if base.endswith("_debug"):
            self.set_override(base)
            return base

        debug_name = f"{base}_debug"

        if not self.exists(debug_name):
            self.fork_for_debug(base)

        self.set_override(debug_name)
        self._set_debug()
        return debug_name

    def get_visible_settings(
        self, profile_name: Optional[str] = None
    ) -> List[Tuple[str, Any, AccesField]]:
        """
        Devuelve una tupla con field_name, value y nivel de acceso de las configuraciones visibles para el profile especificado.
        """
        all_values = self.get_config_values(profile_name)
        visibles = []

        for field_name, _ in Settings.model_fields.items():
            info = Settings.get_info(field_name)
            if info is None:
                continue

            current_value = all_values.get(field_name, info.default_value)
            visibles.append((field_name, current_value, info))

        return visibles
