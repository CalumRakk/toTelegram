import hashlib
import logging
import os
import re
import unicodedata
import uuid
from pathlib import Path
from typing import (
    Annotated,
    Any,
    ClassVar,
    Dict,
    List,
    Literal,
    Tuple,
    cast,
)

from dotenv import dotenv_values
from filelock import FileLock
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo

from totelegram.common.helpers import get_type_annotation, parse_comma_list
from totelegram.schemas import VALUE_NOT_SET, AccessLevel, InfoField
from totelegram.telegram.utils import normalize_chat_id

logger = logging.getLogger(__name__)

# Anotaciones de tipos de Pydantic
IntList = Annotated[
    List[int],
    BeforeValidator(parse_comma_list),
]

CommaSeparatedList = Annotated[
    List[str],
    BeforeValidator(parse_comma_list),
]

ChatID = Annotated[
    int | str,
    BeforeValidator(normalize_chat_id),
]

DEFAULT_TG_API_ID = 611335
DEFAULT_TG_API_HASH = "d524b414d21f4d37f08684c1df41ac9c"

TG_MAX_SIZE_SINGLE_FILE = 2000 * 1024 * 1024  # 2,097,152,000 bytes
TG_MAX_SIZE_MULTIPLE_FILES = 4000 * 1024 * 1024  # 4,194,304,000 bytes


class Settings(BaseModel):
    """Modelo canónico de configuración para un perfil de toTelegram."""

    max_filename_length: ClassVar[int] = 55
    chat_id: ChatID = Field(
        default=VALUE_NOT_SET,
        description="ID del chat destino. NOT_SET indica configuración pendiente.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE},
    )
    exclude_files: CommaSeparatedList = Field(
        default_factory=list,
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE},
        description="Patrones (glob). Ej: '*.log', 'node_modules' (ignora contenido), 'src/*.tmp'.",
    )
    upload_limit_rate_kbps: int = Field(
        default=0,
        description="Límite de velocidad de subida en KB/s. 0 = sin límite",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE},
    )

    api_id: int = Field(
        default=DEFAULT_TG_API_ID,
        description="Telegram API ID",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_READONLY},
    )
    api_hash: str = Field(
        default=DEFAULT_TG_API_HASH,
        description="Telegram API hash",
        json_schema_extra={"is_sensitive": True, "access": AccessLevel.DEBUG_READONLY},
    )
    has_custom_app: bool = Field(
        default=False,
        description="Indica si la cuenta usa credenciales propias de my.telegram.org o las públicas compartidas.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_READONLY},
    )
    profile_name: str = Field(
        default=VALUE_NOT_SET,
        description="Nombre o alias para la cuenta",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_READONLY},
    )

    tg_max_size_normal: int = Field(
        default=TG_MAX_SIZE_SINGLE_FILE,
        description="Límite de tamaño en bytes para usuarios NO premium.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_EDITABLE},
    )
    tg_max_size_premium: int = Field(
        default=TG_MAX_SIZE_MULTIPLE_FILES,
        description="Límite de tamaño en bytes para usuarios premium.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_EDITABLE},
    )

    max_filesize_bytes: int = Field(
        default=120 * 1024 * 1024 * 1024,
        description="Filtro de seguridad: No procesar archivos que superen este tamaño.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE},
    )
    upload_pause_range: IntList = Field(
        default_factory=lambda: [0, 0],
        description="Rango de pausa aleatoria entre subidas (en minutos). Ej: '10,30'. [0,0] para desactivar.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE},
    )

    telegram_account_id: int | None = Field(
        default=None,
        description="ID único de la cuenta de Telegram. Usado para locks distribuidos.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_READONLY},
    )

    auto_truncate: bool = Field(
        default=False,
        description="Trunca automáticamente los nombres largos al empaquetar carpetas en TAR.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE},
    )

    log_path: Path | None = Field(
        default=None,
        description="Ruta del archivo de log. Si está vacío, se usa la ruta por defecto.",
    )

    model_config = ConfigDict(extra="ignore")

    @property
    def profile_alias(self) -> str:
        return self.profile_name

    @field_validator("upload_pause_range", mode="after")
    @classmethod
    def validate_pause_range(cls, v: List[int]) -> List[int]:
        if len(v) == 1:
            return [v[0], v[0]]
        if len(v) > 2:
            return v[:2]
        if not v:
            return [0, 0]
        return v

    @classmethod
    def get_info(cls, field_name: str) -> InfoField | None:
        """Extrae la información de metadatos de un campo de Settings."""
        field: FieldInfo | None = cls.model_fields.get(field_name.lower())
        if field is None:
            return None

        description = field.description or "Sin descripción"

        if field.is_required():
            default_value = "Required"
        elif field.default_factory is not None:
            default_value = field.default_factory()  # type: ignore
        else:
            default_value = field.default

        if not isinstance(field.json_schema_extra, dict):
            return None

        level = cast(
            AccessLevel,
            field.json_schema_extra.get("access", AccessLevel.DEBUG_READONLY),
        )
        is_sensitive = cast(bool, field.json_schema_extra.get("is_sensitive", False))
        type_annotation = get_type_annotation(field)

        return InfoField(
            level=level,
            field_name=field_name,
            description=description,
            default_value=default_value,
            is_sensitive=is_sensitive,
            type_annotation=type_annotation,
        )

    @classmethod
    def validate_single_setting(cls, key: str, value: Any) -> Any:
        """Convierte y valida un valor contra el tipo esperado de la clave."""
        field_name = key.lower()
        if field_name not in cls.model_fields:
            raise ValueError(f"El campo '{key}' no existe en la configuracion.")

        field = cls.model_fields[field_name]
        adapter = TypeAdapter(Annotated[field.annotation, field])
        try:
            return adapter.validate_python(value)
        except ValidationError as e:
            raise ValueError(
                f"La configuración '{key}' debe ser de tipo '{field.annotation}'."
            ) from e

    @staticmethod
    def validate_key_access(is_debug: bool, field_name: str) -> InfoField:
        """Valida que el nivel de acceso permita la modificación del campo (ADR-001)."""
        field_name_lower = field_name.lower()
        info = Settings.get_info(field_name_lower)

        if not info:
            raise ValueError(
                f"La configuracion '{field_name_lower.upper()}' no existe."
            )

        if info.level == AccessLevel.DEBUG_READONLY:
            raise ValueError(
                f"La configuracion '{info.field_name.upper()}' es de identidad (Solo Lectura)."
            )

        if info.level == AccessLevel.DEBUG_EDITABLE and not is_debug:
            raise ValueError(
                f"La configuracion '{info.field_name.upper()}' solo es modificable en modo --debug."
            )
        return info

    @staticmethod
    def get_default_settings() -> "Settings":
        return Settings(profile_name=VALUE_NOT_SET)


class Profile(BaseModel):
    name: str
    path_config: Path
    path_session: Path

    @model_validator(mode="before")
    @classmethod
    def _compat_path_env(cls, data: Any) -> Any:
        """Permite inicializar pasando 'path_env' o 'path_config' indistintamente."""
        if isinstance(data, dict) and "path_env" in data and "path_config" not in data:
            data["path_config"] = data["path_env"]
        return data

    @property
    def path_env(self) -> Path:
        """Alias para compatibilidad con código existente."""
        return self.path_config

    @property
    def has_config(self) -> bool:
        return self.path_config.exists()

    @property
    def has_env(self) -> bool:
        """Alias para compatibilidad con código existente."""
        return self.has_config

    @property
    def has_session(self) -> bool:
        return self.path_session.exists()

    @property
    def is_trinity(self) -> bool:
        """Un perfil es trinity si posee archivo de configuración y sesión física."""
        return self.has_config and self.has_session


class SettingsManager:
    """Administrador centralizado de perfiles y ajustes estructurados (JSON)."""

    def __init__(self, worktable: Path):
        self.worktable = worktable
        self.settings_active_path = self.worktable / "active_profile_name"
        self.profiles_dir = self.worktable / "profiles"
        self.inventories_dir = self.worktable / "inventories"
        self.database_path = self.worktable / f"{self.worktable.name}.sqlite"

        self._worker_instance_id = f"{uuid.uuid4().hex[:8]}:{os.getpid()}"

    @property
    def node_id(self) -> str:
        return self._worker_instance_id

    def _get_all_profile_names(self) -> List[str]:
        """Busca todos los perfiles que tengan .json, .env (legado) o .session."""
        if not self.profiles_dir.exists():
            return []
        stems = {
            f.stem
            for f in self.profiles_dir.glob("*")
            if f.suffix in [".json", ".env", ".session"]
        }
        return sorted(stems)

    def get_all_profiles(self) -> List[Profile]:
        profiles = []
        for name in self._get_all_profile_names():
            path_config = self.get_settings_path(name)
            # Si no existe .json pero sí .env, enlazamos al .env antes de la migración
            if not path_config.exists():
                legacy_env = self.profiles_dir / f"{name}.env"
                if legacy_env.exists():
                    path_config = legacy_env

            path_session = self.get_session_path(name)
            profiles.append(
                Profile(name=name, path_config=path_config, path_session=path_session)
            )
        return profiles

    def get_profile(self, profile_name: str) -> Profile | None:
        return next(
            (p for p in self.get_all_profiles() if p.name == profile_name), None
        )

    def delete_profile(self, profile: Profile) -> List[Path]:
        """Elimina físicamente los archivos del perfil (.json, .env legado y .session)."""
        deleted_files = []

        if profile.path_config.exists():
            profile.path_config.unlink()
            deleted_files.append(profile.path_config)

        legacy_env = self.profiles_dir / f"{profile.name}.env"
        if legacy_env.exists():
            legacy_env.unlink()
            deleted_files.append(legacy_env)

        if profile.path_session.exists():
            db_files = [".session-journal", ".session-shm", ".session-wal"]
            profile.path_session.unlink()
            deleted_files.append(profile.path_session)

            for suffix in db_files:
                related = profile.path_session.with_suffix(suffix)
                if related.exists():
                    related.unlink()
                    deleted_files.append(related)

        if (
            self.has_active_profile_configured()
            and self.get_active_profile_name() == profile.name
        ):
            self.settings_active_path.unlink()

        return deleted_files

    def settings_exists(self, profile_name: str) -> bool:
        json_path = self.get_settings_path(profile_name)
        legacy_env = self.profiles_dir / f"{profile_name}.env"
        return json_path.exists() or legacy_env.exists()

    def set_profile_name_as_active(self, profile_name: str) -> None:
        profile = self.get_profile(profile_name)
        if profile is None:
            raise OSError(
                f"Se intentó activar el perfil '{profile_name}', pero no existe."
            )
        if not profile.is_trinity:
            raise OSError(
                f"Se intentó activar el perfil '{profile_name}', pero está incompleto."
            )

        self.settings_active_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_active_path.write_text(profile_name, encoding="utf-8")

    def get_active_profile_name(self) -> str | None:
        if not self.settings_active_path.exists():
            return None
        return self.settings_active_path.read_text(encoding="utf-8").strip()

    def has_active_profile_configured(self) -> bool:
        if self.settings_active_path.exists():
            return (
                len(self.settings_active_path.read_text(encoding="utf-8").strip()) > 0
            )
        return False

    def get_session_path(self, profile_name: str) -> Path:
        return self.profiles_dir / f"{profile_name}.session"

    def get_settings_path(self, profile_name: str) -> Path:
        """Ruta canónica del archivo de configuración JSON."""
        return self.profiles_dir / f"{profile_name}.json"

    def get_settings(self, profile_name: str) -> Settings:
        """Carga y valida los ajustes desde JSON con migración automática desde .env legado."""
        json_path = self.get_settings_path(profile_name)
        legacy_env_path = self.profiles_dir / f"{profile_name}.env"

        if json_path.is_file():
            try:
                return Settings.model_validate_json(
                    json_path.read_text(encoding="utf-8")
                )
            except ValidationError as e:
                logger.error(f"Error de validación en '{json_path.name}': {e}")
                raise ValueError(f"Error de validacion en '{json_path.name}': {e}")

        # Migración transparente de .env legado a .json moderno
        if legacy_env_path.is_file():
            return self._migrate_env_to_json(profile_name, legacy_env_path, json_path)

        raise OSError(f"El archivo de configuración para '{profile_name}' no existe.")

    def _migrate_env_to_json(
        self, profile_name: str, env_path: Path, json_path: Path
    ) -> Settings:
        """Migra de forma transparente un archivo .env legado al nuevo formato JSON."""
        raw_values = dotenv_values(env_path)
        data = {}
        for k, v in raw_values.items():
            if v is None:
                continue
            k_norm = k.lower().strip()
            if k_norm in Settings.model_fields:
                try:
                    data[k_norm] = Settings.validate_single_setting(k_norm, v)
                except Exception:  # noqa: S110
                    pass

        settings = Settings(**data)
        self.save_settings(profile_name, settings)

        try:
            env_path.unlink()
            logger.info(
                f"Perfil '{profile_name}' migrado exitosamente de .env a .json estructurado."
            )
        except OSError:
            pass

        return settings

    def save_settings(self, profile_name: str, settings: Settings) -> None:
        """Guarda atómicamente la configuración en formato JSON legible."""
        json_path = self.get_settings_path(profile_name)
        json_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = json_path.with_name(f".{json_path.name}.tmp_{os.getpid()}")
        temp_path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
        temp_path.replace(json_path)

    def set_setting(self, profile_name: str, field_name: str, field_value: Any) -> bool:
        """Actualiza un ajuste individual de forma tipada."""
        key = field_name.lower().strip()
        settings = self.get_settings(profile_name)
        current_value = getattr(settings, key, None)

        if current_value == field_value:
            return False

        setattr(settings, key, field_value)
        self.save_settings(profile_name, settings)
        return True

    def unset_setting(self, profile_name: str, field_name: str) -> bool:
        """Restaura un ajuste a su valor por defecto."""
        key = field_name.lower().strip()
        info = Settings.get_info(key)
        if not info:
            return False

        settings = self.get_settings(profile_name)
        current_value = getattr(settings, key, None)
        default_value = info.default_value

        if current_value == default_value:
            return False

        setattr(settings, key, default_value)
        self.save_settings(profile_name, settings)
        return True

    def _write_all_settings(self, profile_name: str, data: Dict[str, Any]) -> None:
        """Método de compatibilidad para inicializar perfiles masivamente desde auth/tests."""
        sanitized = {}
        for k, v in data.items():
            k_norm = k.lower().strip()
            if k_norm in Settings.model_fields:
                sanitized[k_norm] = Settings.validate_single_setting(k_norm, v)

        settings = Settings(**sanitized)
        self.save_settings(profile_name, settings)

    def resolve_profile_name(
        self, profile_name: str | None = None, strict: bool = True
    ) -> str | None:
        """Resuelve el nombre del perfil respetando la prioridad y existencia."""
        if profile_name:
            if not self.settings_exists(profile_name) and strict:
                raise ValueError(f"El perfil {profile_name} no existe.")
            return profile_name

        if not self.has_active_profile_configured():
            if strict:
                raise ValueError("No hay un perfil activo.")
            return None

        active = self.get_active_profile_name()
        if active:
            if not self.settings_exists(active):
                msg = f"El perfil activo '{active}' no es válido porque su archivo de configuración no existe."
                if strict:
                    raise ValueError(msg)
                return None
            return active

        if strict:
            raise ValueError("No hay un perfil activo.")
        return None


class ConfigService:
    """Servicio desacoplado para operaciones sobre la configuración."""

    def __init__(self, manager: SettingsManager, is_debug: bool = False):
        self.manager = manager
        self.is_debug = is_debug

    def prepare_updates(self, args: List[str]) -> Dict[str, Any]:
        """Convierte argumentos CLI planos en un diccionario validado de pares clave-valor."""
        if not args or len(args) % 2 != 0:
            raise ValueError(
                "Debes proporcionar pares de CLAVE y VALOR. Ej: 'set chat_id 12345'"
            )

        updates = {}
        for i in range(0, len(args), 2):
            raw_key = args[i].lower().strip()
            raw_val = args[i + 1]
            if isinstance(raw_val, str):
                raw_val = raw_val.strip("'").strip('"')

            Settings.validate_key_access(self.is_debug, raw_key)
            clean_val = Settings.validate_single_setting(raw_key, raw_val)
            updates[raw_key] = clean_val

        return updates

    def apply_set(self, profile_name: str, key: str, value: Any) -> Tuple[bool, Any]:
        """Aplica una actualización sobre un ajuste atómico."""
        changed = self.manager.set_setting(profile_name, key, value)
        return changed, value

    def modify_list_setting(
        self,
        profile_name: str,
        key: str,
        items: List[str],
        action: Literal["add", "remove"],
    ) -> Tuple[bool, List[str]]:
        """Añade o elimina elementos de una configuración de tipo lista de forma atómica."""
        key = key.lower().strip()
        Settings.validate_key_access(self.is_debug, key)

        settings = self.manager.get_settings(profile_name)
        current_list = getattr(settings, key, None)

        if not isinstance(current_list, list):
            raise TypeError(
                f"El campo '{key}' no es una lista, no se puede usar '{action}'."
            )

        original_count = len(current_list)
        new_list = list(current_list)

        if action == "add":
            for item in items:
                clean_item = item.strip("'").strip('"')
                if clean_item not in new_list:
                    new_list.append(clean_item)
        elif action == "remove":
            clean_items = {i.strip("'").strip('"') for i in items}
            new_list = [i for i in new_list if i not in clean_items]

        changed = len(new_list) != original_count
        if changed:
            self.manager.set_setting(profile_name, key, new_list)

        return changed, new_list

    def restore_default(self, settings_name: str, key: str) -> Any:
        """Restaura una configuración a su valor por defecto."""
        info = Settings.validate_key_access(self.is_debug, key)
        self.manager.unset_setting(settings_name, key)
        return info.default_value


def sanitize_alias(raw_name: str, max_length: int = 15) -> str:
    """Sanitiza una cadena para generar un alias seguro de perfil."""
    if not raw_name:
        return "user"

    normalized = unicodedata.normalize("NFKD", str(raw_name))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")

    clean = re.sub(r"[\s\-]+", "_", ascii_text.strip().lower())
    clean = re.sub(r"[^a-z0-9_]", "", clean)
    clean = re.sub(r"_+", "_", clean).strip("_")

    return clean[:max_length] or "user"


def generate_unique_alias(
    base_alias: str, existing_aliases: list[str], max_length: int = 15
) -> str:
    """Genera un alias garantizando unicidad contra una lista existente."""
    clean = sanitize_alias(base_alias, max_length=max_length)
    existing_set = {a.lower() for a in existing_aliases}

    if clean not in existing_set:
        return clean

    counter = 2
    while f"{clean}_{counter}" in existing_set:
        counter += 1

    return f"{clean}_{counter}"
