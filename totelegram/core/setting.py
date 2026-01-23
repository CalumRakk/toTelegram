import os
import re
import sys
from os import getenv
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Set, Union, cast, get_origin

from pydantic import Field, TypeAdapter, ValidationError, field_validator
from pydantic_settings import BaseSettings

from totelegram.core.enums import DuplicatePolicy

CHAT_ID_NOT_SET = "NOT_SET"


def normalize_chat_id(value: str) -> Union[int, str]:
    """
    Normaliza un identificador de chat de Telegram.

    Acepta alias ("me", "self"), IDs numéricos positivos o negativos,
    y usernames con o sin "@". El resultado se devuelve en un formato
    estándar compatible con la API de Telegram.

    Logica:
    - "me" / "self" -> "me"
    - IDs numéricos -> int
    - Usernames válidos -> "@username"

    Args:
        value (str): Identificador de destino a normalizar.

    Returns:
        Union[int, str]: Identificador normalizado.

    Raises:
        ValueError: Si el valor no corresponde a un destino válido.
    """

    raw = str(value).strip()
    if raw.upper() == CHAT_ID_NOT_SET or not raw:
        return CHAT_ID_NOT_SET

    if raw.lower() in ["me", "self"]:
        return "me"

    clean_numeric = raw.replace("-", "")
    if clean_numeric.isdigit():
        return int(raw)

    clean_username = raw.lstrip("@")

    if re.match(r"^[a-zA-Z0-9_]+$", clean_username):
        return f"@{clean_username}"

    raise ValueError(
        f"El destino '{raw}' no es válido. Debe ser 'me', un ID numérico "
        "o un @username (ej: caracoltv)."
    )


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
    profile_name: str = Field(description="Nombre de la sesión")
    chat_id: Union[str, int] = Field(
        default=CHAT_ID_NOT_SET,
        description="ID del chat destino. NOT_SET indica configuración pendiente.",
    )
    api_hash: str = Field(
        description="Telegram API hash", default="d524b414d21f4d37f08684c1df41ac9c"
    )
    api_id: int = Field(description="Telegram API ID", default=611335)

    max_filesize_bytes: int = Field(
        default=80 * 1024 * 1024 * 1024,
        description="Filtro de seguridad: No procesar archivos que superen este tamaño.",
    )
    upload_limit_rate_kbps: int = Field(
        default=0, description="Límite de velocidad de subida en KB/s. 0 = sin límite"
    )

    # TODO: agrega un default que impida subir archivo muy pequeños.

    exclude_files: List[str] = Field(
        default=[],
        description="Patrones (glob). Ej: '*.log', 'node_modules' (ignora contenido), 'src/*.tmp'.",
    )

    duplicate_policy: DuplicatePolicy = Field(
        default=DuplicatePolicy.SMART,
        description="Gobernanza de duplicados: 'smart', 'strict' o 'force'.",
    )

    app_name: str = "toTelegram"
    database_name: str = f"{app_name}.sqlite"
    exclude_files_default: List[str] = ["*.log", "*.json", "*.json.xz"]
    max_filename_length: int = 55

    worktable: Path = Field(
        default=Path(get_user_config_dir(app_name)).resolve(),
        description="Carpeta de trabajo para la aplicación, donde se almacena la db y perfiles",
    )
    log_path: Optional[Path] = Field(
        default=None,
        description="Ruta del archivo de log. Si está vacío, se usa la ruta por defecto en la carpeta de trabajo.",
    )

    TG_MAX_SIZE_NORMAL: int = 2_097_152_000  # ~2GB
    # ~4GB TODO : confirmar si el valor funciona
    TG_MAX_SIZE_PREMIUM: int = 4000 * 1024 * 1024

    def model_post_init(self, __context):
        if self.log_path is None:
            self.log_path = self.worktable / "app_name.log"

    # ClassVar asegura que Pydantic ignore esto al validar datos.
    INTERNAL_FIELDS: ClassVar[Set[str]] = {
        "APP_NAME",
        "LOG_PATH",
        "DATABASE_NAME",
        "EXCLUDE_FILES_DEFAULT",
        "PROFILE_NAME",
        "API_HASH",
        "API_ID",
        "MAX_FILENAME_LENGTH",
        "TG_MAX_SIZE_NORMAL",
        "TG_MAX_SIZE_PREMIUM",
    }
    SENSITIVE_FIELDS: ClassVar[Set[str]] = {"API_HASH", "API_ID"}

    @property
    def database_path(self) -> Path:
        return self.worktable / self.database_name

    @property
    def profile_path(self):
        return self.worktable / "profiles"

    @classmethod
    def get_schema_info(cls) -> List[Dict[str, str]]:
        """
        Retorna una lista con la metadata de cada configuración disponible.
        Útil para mostrar ayuda al usuario.
        """
        info = []
        for name, field in cls.model_fields.items():
            if name.upper() in cls.INTERNAL_FIELDS:
                continue

            type_annotation = field.annotation
            if get_origin(type_annotation) is None:
                type_name = type_annotation.__name__  # type: ignore
            else:
                type_name = str(type_annotation).replace("typing.", "")

            desc = field.description or "Sin descripción"
            default_val = field.default

            # Si el default es PydanticUndefined, no mostrar nada
            if str(default_val) == "PydanticUndefined":
                default_val = "(Requerido)"

            info.append(
                {
                    "key": name.upper(),
                    "type": type_name,
                    "description": desc,
                    "default": str(default_val),
                }
            )
        return info

    @classmethod
    def validate_single_setting(cls, key: str, value: str) -> Any:
        """
        Valida un solo campo simulando su inyección desde variable de entorno.
        Lanza ValidationError si falla.
        Retorna el valor python casteado si es correcto.
        """
        field_name = key.lower()
        if field_name not in cls.model_fields:
            raise ValueError(f"La configuración '{key}' no existe en el sistema.")

        field_info = cls.model_fields[field_name]
        target_type = field_info.annotation

        adapter = TypeAdapter(target_type)

        # Intentamos validar. Pydantic intentará convertir "true" a True, "123" a 123, etc.
        return adapter.validate_python(value)

    @field_validator("chat_id", mode="before")
    @classmethod
    def semantic_chat_id_normalizer(cls, v: Any) -> Union[str, int]:
        return normalize_chat_id(str(v))


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
