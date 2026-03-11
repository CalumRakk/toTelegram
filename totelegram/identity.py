import json
import logging
from pathlib import Path
from typing import Annotated, Any, ClassVar, Dict, List, Literal, Optional, Tuple, cast

from dotenv import dotenv_values
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

from totelegram.schemas import VALUE_NOT_SET, AccessLevel, InfoField
from totelegram.utils import ChatID, CommaSeparatedList, IntList, get_type_annotation

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # TODO: agrega un default que impida subir archivo muy pequeños.

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
        default=611335,
        description="Telegram API ID",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_READONLY},
    )
    api_hash: str = Field(
        default="d524b414d21f4d37f08684c1df41ac9c",
        description="Telegram API hash",
        json_schema_extra={"is_sensitive": True, "access": AccessLevel.DEBUG_READONLY},
    )
    profile_name: str = Field(
        description="Nombre de la sesión",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_READONLY},
    )

    tg_max_size_normal: int = Field(
        default=2000 * 1024 * 1024,
        description="Limite de tamaño en bytes para usuarios NO premium.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_EDITABLE},
    )
    tg_max_size_premium: int = Field(
        default=4000 * 1024 * 1024,
        description="Límite de tamaño en bytes para usuarios premium.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_EDITABLE},
    )

    max_filesize_bytes: int = Field(
        default=80 * 1024 * 1024 * 1024,
        description="Filtro de seguridad: No procesar archivos que superen este tamaño.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE},
    )
    upload_pause_range: IntList = Field(
        default_factory=lambda: [0, 0],
        description="Rango de pausa aleatoria entre subidas (en minutos). Ej: '10,30'. [0,0] para desactivar.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE},
    )

    # exclude_files_default: CommaSeparatedList = ["*.json", "*.json.xz"]

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    log_path: Optional[Path] = Field(
        default=None,
        description="Ruta del archivo de log. Si está vacío, se usa la ruta por defecto en la carpeta de trabajo.",
    )

    @field_validator("upload_pause_range", mode="after")
    @classmethod
    def validate_pause_range(cls, v: List[int]) -> List[int]:
        if len(v) == 1:
            return [v[0], v[0]]  # Si pone '30', la pausa es fija de 30 min.
        if len(v) > 2:
            return v[:2]  # Ignorar si pone más de dos números
        if not v:
            return [0, 0]
        return v

    @classmethod
    def get_info(cls, field_name: str) -> Optional[InfoField]:
        """Extrae la informacion de un campo de Settings.

        Si un campo no tiene access, se considerárá DEBUG_READONLY.
        """
        field: FieldInfo | None = cls.model_fields.get(field_name.lower())

        assert (
            field is not None
        ), f"El campo '{field_name}' no existe en la configuración."

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
        """
        Resuelve, convierte y valida el valor asociado a una clave de configuración.

        Actúa como puente entre entradas en texto plano (por ejemplo, desde una CLI)
        y el sistema de tipos de Pydantic, aplicando coerción automática al tipo
        definido (p. ej., "500" → 500, o una cadena separada por comas → lista).

        Args:
            key (str): Nombre de la configuración (case-insensitive).
            value (Any): Valor crudo a procesar.

        Returns:
            Any: Valor convertido al tipo definido en el modelo Settings.

        Raises:
            ValueError: Si la clave no existe o el valor no puede convertirse
                al tipo requerido.
        """
        field_name = key.lower()
        if field_name not in cls.model_fields:
            raise ValueError(f"El campo '{key}' no existe en la configuracion.")

        field = cls.model_fields[field_name]
        adapter = TypeAdapter(Annotated[field.annotation, field])
        try:
            # validate_python intentará convertir `value` al tipo correcto (int, bool, etc.)
            return adapter.validate_python(value)
        except ValidationError as e:
            raise ValueError(
                f"La configuración '{key}' debe ser de tipo '{field.annotation}'."
            ) from e

    @staticmethod
    def validate_key_access(is_debug: bool, field_name: str):
        """Valida si el nivel de acceso permite la edición en el contexto actual."""
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
    path_env: Path
    path_session: Path

    @property
    def has_env(self):
        return self.path_env.exists()

    @property
    def has_session(self):
        return self.path_session.exists()

    @property
    def is_trinity(self):
        """Un perfil es trinity si tiene ambos archivos y su nombre."""
        return self.has_env and self.has_session


class SettingsManager:
    def __init__(self, worktable: Path):
        self.worktable = worktable
        self.settings_active_path = self.worktable / "active_profile_name"
        self.profiles_dir = self.worktable / "profiles"
        self.inventories_dir = self.worktable / "inventories"
        self.database_path = self.worktable / f"{self.worktable.name}.sqlite"

    def _get_all_profile_names(self) -> List[str]:
        """Busca todos los nombres únicos que tienen un .env o un .session"""
        if not self.profiles_dir.exists():
            return []

        stems = set()
        for f in self.profiles_dir.glob("*"):
            if f.suffix in [".env", ".session"]:
                stems.add(f.stem)
        return sorted(list(stems))

    def get_all_profiles(self) -> List[Profile]:
        profiles = []
        for name in self._get_all_profile_names():
            path_env = self.get_settings_path(name)
            path_session = self.get_session_path(name)
            profile = Profile(name=name, path_env=path_env, path_session=path_session)
            profiles.append(profile)
        return profiles

    def get_profile(self, profile_name: str) -> Optional[Profile]:
        """Busca un perfil por su nombre (case-sensitive)"""
        return next(
            (p for p in self.get_all_profiles() if p.name == profile_name), None
        )

    def delete_profile(self, profile: Profile) -> List[Path]:
        """
        Elimina físicamente el conjunto de archivos del perfil.
        Retorna una lista de los archivos que fueron eliminados.
        """
        env_path = profile.path_env
        session_path = profile.path_session

        deleted_files = []

        if env_path.exists():
            env_path.unlink()
            deleted_files.append(env_path)

        if session_path.exists():
            db_files = [".session-journal", ".session-shm", ".session-wal"]
            session_path.unlink()
            deleted_files.append(session_path)

            for suffix in db_files:
                related_db = session_path.with_suffix(suffix)
                if related_db.exists():
                    related_db.unlink()
                    deleted_files.append(related_db)

        if self.has_active_profile_configured():
            if self.get_active_profile_name() == profile.name:
                self.settings_active_path.unlink()

        return deleted_files

    def settings_exists(self, profile_name: str) -> bool:
        return self.get_settings_path(profile_name).exists()

    def set_profile_name_as_active(self, profile_name: str) -> None:
        """
        Establece un perfil como activo.

        Valida que el perfil exista y que sea "trinity" (completo y apto para uso global).
        Un perfil incompleto no puede activarse porque rompería la coherencia del entorno.

        Args:
            profile_name (str): Nombre del perfil a activar
        Raises:
            IOError: Si el perfil no existe o no es "trinity"
        """
        profile = self.get_profile(profile_name)

        if profile is None:
            raise IOError(
                f"Se intentó activar el perfil '{profile_name}', pero no existe."
            )
        if not profile.is_trinity:
            raise IOError(
                f"Se intentó activar el perfil '{profile_name}', pero no es trinity."
            )

        self.settings_active_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_active_path.write_text(profile_name)

    def get_active_profile_name(self) -> Optional[str]:
        if not self.settings_active_path.exists():
            return None
        return self.settings_active_path.read_text().strip()

    def has_active_profile_configured(self) -> bool:
        """
        Indica si hay un perfil activo configurado.
        """
        if self.settings_active_path.exists():
            content = self.settings_active_path.read_text().strip()
            return len(content) > 0
        return False

    def get_settings(self, profile_name: str) -> Settings:

        settings_path = self.get_settings_path(profile_name)
        if not settings_path.exists():
            raise IOError(
                f"El archivo de configuracion '{settings_path.name}' no existe."
            )

        try:
            settings = Settings(_env_file=settings_path)  # type: ignore
            return settings
        except (ValueError, ValidationError) as e:
            logger.error(
                f"El archivo de configuracion '{settings_path.name}' contiene errores:"
            )
            raise ValueError(f"Error de validacion: {e}")

    def resolve_profile_name(
        self, profile_name: Optional[str] = None, strict: bool = True
    ) -> Optional[str]:
        """
        Valida y resuelve el perfil de configuración a utilizar.
        La operacion simplifica estos tres escenarios:

        - Si se proporciona `profile_name`, verifica que exista el archivo `.env`.
        - Si no se proporciona, utiliza el perfil activo guardado en `worktable/active_profile_name`.
        - Si la validación falla y `strict` es True, lanza ValueError;
        de lo contrario, devuelve None.

        Args:
            profile_name (Optional[str]): Nombre del perfil a validar.
            error (bool): Indica si se debe lanzar una excepción en caso de error.

        Raises:
            ValueError: Si no se logra resolver el perfil y `error` es True.
        """

        if profile_name:
            if not self.settings_exists(profile_name):
                logger.debug(f"El perfil {profile_name} no existe.")
                if strict:
                    raise ValueError(f"El perfil {profile_name} no existe.")
            return profile_name

        if not self.has_active_profile_configured():
            logger.debug("No hay un perfil activo.")
            if strict:
                raise ValueError("No hay un perfil activo.")

        active = self.get_active_profile_name()
        if active:
            # FIX: validar que el perfil activo tenga un .env válido antes de devolverlo.
            if not self.settings_exists(active):
                msg = f"El perfil activo '{active}' no es válido porque su archivo de configuración no existe."
                logger.debug(msg)
                if strict:
                    raise ValueError(msg)
                return None
            return active

        if strict:
            raise ValueError("No hay un perfil activo.")

    def get_session_path(self, profile_name: str) -> Path:
        return self.profiles_dir / f"{profile_name}.session"

    # --------------------------------
    #  Métodos de escritura y sanidad en el contexto de `.env`
    #  En este contexto solo existe settings_name
    # --------------------------------

    # TODO: analizar si deberia cambiar los métodos settings_* por env_*
    def get_settings_path(self, profile_name: str) -> Path:
        """profile_name -> profiles/profile_name.env"""
        return self.profiles_dir / f"{profile_name}.env"

    def _load_and_sanitize(self, settings_name: str) -> Dict[str, Any]:
        """
        Carga el contenido crudo del .env y normaliza sus claves.

        ¿Por qué este método y no usar Settings() para cargarlo? por:

         1. Evita la contaminacion del archivo: Settings cargaría todos los valores por defecto
            del sistema. Al guardar, el archivo (.model_dump()) pasaría de tener 3 líneas
            a tener +20, dificultando la lectura humana.
         2. Resiliencia: Si al archivo le faltan campos obligatorios (ej. profile_name),
            Settings() lanzaría una validación fallida impidiendo incluso la lectura.
            Este método permite cargar archivos "rotos" para repararlos.
         3. Integridad: Evita que Pydantic descarte variables o metadatos presentes
            en el archivo que no estén mapeados en el modelo actual.
        """
        path = self.get_settings_path(settings_name)
        if not path.exists():
            return {}

        # dotenv_values devuelve todos los valores como strings.
        raw_values = dotenv_values(path)
        return {
            k.lower(): Settings.validate_single_setting(k, v)
            for k, v in raw_values.items()
            if v is not None
        }

    def _write_all_settings(self, settings_name: str, data: Dict[str, Any]) -> None:
        """Vuelca un diccionario completo al archivo .env

        No verifica claves ni valor.
        Se espera que el valor sea un objeto estandar de python.
        """
        path = self.get_settings_path(settings_name)
        path.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            f"{k.lower()}={json.dumps(v, ensure_ascii=False)}" for k, v in data.items()
        ]

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def set_setting(
        self, settings_name: str, field_name: str, field_value: Any
    ) -> bool:
        """
        Guarda un valor directamente en el archivo .env.
        Asume que el valor ya fue validado y sanitizado por la capa superior.
        Devuelve True si hubo un cambio real en el archivo.
        """
        key_normalized = field_name.lower().strip()
        current_data = self._load_and_sanitize(settings_name)

        # Evitar escritura innecesaria si el valor es el mismo
        if (
            key_normalized in current_data
            and current_data[key_normalized] == field_value
        ):
            return False

        current_data[key_normalized] = field_value
        self._write_all_settings(settings_name, current_data)
        return True

    def unset_setting(self, settings_name: str, field_name: str) -> bool:
        """
        Elimina una clave del archivo .env para que el sistema use su valor por defecto.
        Devuelve True si la clave existía y fue eliminada.
        """
        key_normalized = field_name.lower().strip()
        current_data = self._load_and_sanitize(settings_name)

        if key_normalized in current_data:
            del current_data[key_normalized]
            self._write_all_settings(settings_name, current_data)
            return True

        return False


class ConfigService:
    def __init__(self, manager: SettingsManager, is_debug: bool = False):
        self.manager = manager
        self.is_debug = is_debug

    def prepare_updates(self, args: List[str]) -> Dict[str, Any]:
        """
        Transforma una lista plana [k, v, k, v] en un dict validado.
        Lanza ValueError si los pares están incompletos o fallan validación.
        """

        # TODO: Hacer más explicito que el valor del par puede ser Any, aunque normalmente se espera que sea un string.
        if not args or len(args) % 2 != 0:
            raise ValueError(
                "Debes proporcionar pares de CLAVE y VALOR. Ej: 'set chat_id 12345'"
            )

        def remove_quotation(value: Any):
            if isinstance(value, str):
                return value.strip("'").strip('"')
            if isinstance(value, list):
                return [remove_quotation(v) for v in value]
            return value

        raw_data = {
            args[i].lower(): remove_quotation(args[i + 1])
            for i in range(0, len(args), 2)
        }
        updates = {}

        for key, raw_value in raw_data.items():
            # Validar permisos de acceso (Editable, Debug, etc)
            Settings.validate_key_access(self.is_debug, key)
            # Validar y convertir tipos (str -> int, etc)
            clean_value = Settings.validate_single_setting(key, raw_value)
            updates[key] = clean_value

        return updates

    def apply_update(
        self,
        settings_name: str,
        key: str,
        value: Any,
        action: Literal["set", "add", "remove"],
    ) -> Tuple[bool, Any]:
        """
        Aplica un cambio individual, resolviendo la lógica de listas si es necesario.
        Devuelve (si_cambio, valor_final).
        """
        key = key.lower().strip()

        if action == "set":
            changed = self.manager.set_setting(settings_name, key, value)
            return changed, value

        # Modificar listas (add/remove)
        if not isinstance(value, list):
            value = [value]

        try:
            current_settings = self.manager.get_settings(settings_name)
            current_list = getattr(current_settings, key)
        except Exception:
            # Si el archivo está roto, tomamos el default
            info = Settings.get_info(key)
            current_list = (
                info.default_value.copy()
                if info and isinstance(info.default_value, list)
                else []
            )

        if not isinstance(current_list, list):
            raise ValueError(
                f"El campo '{key}' no es una lista, no se puede usar '{action}'."
            )

        original_count = len(current_list)

        if action == "add":
            for item in value:
                if item not in current_list:
                    current_list.append(item)
        elif action == "remove":
            current_list = [item for item in current_list if item not in value]

        # Guardar solo si hubo cambios reales
        changed = len(current_list) != original_count
        if changed:
            self.manager.set_setting(settings_name, key, current_list)

        return changed, current_list

    def restore_default(self, settings_name: str, key: str) -> Any:
        """Restaura una configuración a su valor por defecto."""
        info = Settings.validate_key_access(self.is_debug, key)

        self.manager.unset_setting(settings_name, key)

        self.manager.set_setting(settings_name, key, info.default_value)
        return info.default_value
