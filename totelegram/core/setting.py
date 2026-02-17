from enum import IntEnum
from pathlib import Path
from typing import Annotated, Any, ClassVar, List, Optional, Union, cast, get_origin

from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined
from pydantic_settings import BaseSettings, SettingsConfigDict

from totelegram.core.enums import DuplicatePolicy
from totelegram.utils import CHAT_ID_NOT_SET, get_user_config_dir


class AccessLevel(IntEnum):
    EDITABLE = 1                  # Visible y editable
    DEBUG_EDITABLE = 2            # Visible y editable en DEBUG
    DEBUG_READONLY = 3            # Visible en DEBUG (solo lectura)

class AccesField(BaseModel):
    level: AccessLevel
    field_name:str
    description: Optional[str]
    default_value: Any
    is_sensitive: bool
    type_annotation: str

def get_type_annotation(field: FieldInfo) -> str:
    type_annotation = field.annotation
    if get_origin(type_annotation) is None:
        type_name = type_annotation.__name__  # type: ignore
    else:
        type_name = str(type_annotation).replace("typing.", "")
    return type_name

class Settings(BaseSettings):
    # TODO: agrega un default que impida subir archivo muy pequeños.
    APP_NAME: ClassVar[str] = "toTelegram"
    MAX_FILENAME_LENGTH: ClassVar[int] = 55
    database_name: str = f"{APP_NAME}.sqlite"

    chat_id: str = Field(default=CHAT_ID_NOT_SET, description="ID del chat destino. NOT_SET indica configuración pendiente.", json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE})
    exclude_files: list[str] = Field(default=[], json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE},description="Patrones (glob). Ej: '*.log', 'node_modules' (ignora contenido), 'src/*.tmp'.")
    upload_limit_rate_kbps: int = Field(default=0, description="Límite de velocidad de subida en KB/s. 0 = sin límite",json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE})

    api_id: int = Field(default=611335, description="Telegram API ID", json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_READONLY})
    api_hash: str = Field( default="d524b414d21f4d37f08684c1df41ac9c", description="Telegram API hash", json_schema_extra={"is_sensitive": True, "access": AccessLevel.DEBUG_READONLY})
    profile_name: str = Field(description="Nombre de la sesión", json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_READONLY})

    tg_max_size_normal: int = Field(default=2000 * 1024 * 1024, description="Limite de tamaño para usuarios NO premium.",json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_EDITABLE})
    tg_max_size_premium: int = Field(default=4000 * 1024 * 1024,description="Límite de tamaño para usuarios premium.",json_schema_extra={"is_sensitive": False, "access": AccessLevel.DEBUG_EDITABLE})

    max_filesize_bytes: int = Field(default=80 * 1024 * 1024 * 1024,description="Filtro de seguridad: No procesar archivos que superen este tamaño.", json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE})
    duplicate_policy: DuplicatePolicy = Field(default=DuplicatePolicy.SMART,description="Gobernanza de duplicados: 'smart', 'strict' o 'force'.", json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE})
    exclude_files_default: List[str] = ["*.log", "*.json", "*.json.xz"]
    worktable: Path = Field(default=Path(get_user_config_dir(APP_NAME)).resolve(),description="Carpeta de trabajo para la aplicación, donde se almacena la db y perfiles")

    model_config = SettingsConfigDict(env_file=".env",env_file_encoding="utf-8",extra="ignore")


    log_path: Optional[Path] = Field(
        default=None,
        description="Ruta del archivo de log. Si está vacío, se usa la ruta por defecto en la carpeta de trabajo.",
    )


    # # ClassVar asegura que Pydantic ignore esto al validar datos.
    # INTERNAL_FIELDS: ClassVar[Set[str]] = {
    #     "APP_NAME",
    #     "LOG_PATH",
    #     "DATABASE_NAME",
    #     "EXCLUDE_FILES_DEFAULT",
    #     "PROFILE_NAME",
    #     "API_HASH",
    #     "API_ID",
    #     "MAX_FILENAME_LENGTH",
    #     "TG_MAX_SIZE_NORMAL",
    #     "TG_MAX_SIZE_PREMIUM",
    # }
    # SENSITIVE_FIELDS: ClassVar[Set[str]] = {"API_HASH", "API_ID"}

    def model_post_init(self, __context):
        if self.log_path is None:
            self.log_path = self.worktable / "app_name.log"

    @classmethod
    def get_info(cls, field_name: str) -> Optional[AccesField]:
        """Extrae la informacion de un campo de Settings.

        Si un campo no tiene access, se considerárá SYSTEM.
        """
        field : FieldInfo | None= cls.model_fields.get(field_name.lower())

        assert field is not None, f"El campo '{field_name}' no existe en la configuración."

        description= field.description or "Sin descripción"

        # Si un campo no tiene un valor por defecto, se considera requerido.
        default_value= "Required" if field.default is PydanticUndefined else field.default

        if not isinstance(field.json_schema_extra, dict):
            return None

        level = cast(AccessLevel, field.json_schema_extra.get("access", AccessLevel.DEBUG_READONLY))
        is_sensitive= cast(bool, field.json_schema_extra.get("is_sensitive", False))
        type_annotation= get_type_annotation(field)

        return AccesField(
            level=level,
            field_name=field_name,
            description=description,
            default_value=default_value,
            is_sensitive=is_sensitive,
            type_annotation= type_annotation
        )

    @classmethod
    def validate_single_setting(cls, key: str, value: Any) -> Any:
        """
        Valida un solo campo de forma aislada respetando tipos y restricciones (Field).
        """
        field_name = key.lower()
        if field_name not in cls.model_fields:
            raise ValueError(f"La configuración '{key}' no existe en el sistema.")

        field = cls.model_fields[field_name]
        adapter = TypeAdapter(Annotated[field.annotation, field])

        return adapter.validate_python(value)

    # --- Propiedades ----

    @property
    def database_path(self) -> Path:
        return self.worktable / self.database_name

    @property
    def profile_path(self):
        return self.worktable / "profiles"


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
        print("Error en configuración:", e)
        raise
