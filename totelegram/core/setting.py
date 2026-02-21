import json
from enum import IntEnum
from pathlib import Path
from typing import Annotated, Any, ClassVar, List, Optional, Union, cast, get_origin

from pydantic import BaseModel, BeforeValidator, Field, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

from totelegram.core.enums import DuplicatePolicy
from totelegram.utils import VALUE_NOT_SET, normalize_chat_id

APP_SESSION_NAME = "toTelegram"


class AccessLevel(IntEnum):
    EDITABLE = 1  # Visible y editable
    DEBUG_EDITABLE = 2  # Visible y editable en DEBUG
    DEBUG_READONLY = 3  # Visible en DEBUG (solo lectura)


class InfoField(BaseModel):
    level: AccessLevel
    field_name: str
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


def parse_comma_list(value):
    """Convierte 'a, b, c' o '["a", "b"]' en una lista real."""
    # TODO: esta pensado para valores que esta en el archivo de configuracion. Para valores del cli no deberia ser tan permisivo.
    if isinstance(value, list):
        return value

    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("Formato JSON invalido para la lista.")

    # Split por comas y limpieza de espacios
    return [item.strip() for item in value.split(",") if item.strip()]


CommaSeparatedList = Annotated[
    List[str],
    BeforeValidator(parse_comma_list),
]

ChatID = Annotated[
    Union[int, str],
    BeforeValidator(normalize_chat_id),
]


class Settings(BaseSettings):
    # TODO: agrega un default que impida subir archivo muy pequeños.

    MAX_FILENAME_LENGTH: ClassVar[int] = 55
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

    duplicate_policy: DuplicatePolicy = Field(
        default=DuplicatePolicy.SMART,
        description="Gobernanza de duplicados: 'smart', 'strict' o 'force'.",
        json_schema_extra={"is_sensitive": False, "access": AccessLevel.EDITABLE},
    )

    exclude_files_default: CommaSeparatedList = ["*.log", "*.json", "*.json.xz"]

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    log_path: Optional[Path] = Field(
        default=None,
        description="Ruta del archivo de log. Si está vacío, se usa la ruta por defecto en la carpeta de trabajo.",
    )

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
            raise ValueError(f"La configuración '{key}' no existe en el sistema.")

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
