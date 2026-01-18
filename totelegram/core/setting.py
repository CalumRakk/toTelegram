import os
import sys
from os import getenv
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Set, Union, cast, get_origin

from pydantic import Field, TypeAdapter, ValidationError, field_validator
from pydantic_settings import BaseSettings


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
        ..., description="ID del chat o enlace de invitación"
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
    # FIX: QUITAR LA CAPACIDAD DE IGNORAR CARPETAS, NO FUNCIONA CON LA LOGICA DEL PROGRAMA.
    # TODO: agrega un default que impida subir archivo muy pequeños.
    exclude_files: List[str] = Field(
        default=[],
        description="Patrones (glob). Ej: '*.log', 'node_modules' (ignora contenido), 'src/*.tmp'.",
    )
    duplicate_policy: str = Field(
        default="strict",
        description="Politica de Duplicados: 'smart' (preguntar), 'strict' (omitir), 'force' (sube/reenvia).",
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

    def is_excluded(self, path: Path) -> bool:
        """
        Devuelve True si el archivo o alguna de sus carpetas padre
        coincide con los patrones de exclusión.
        """
        if not self.exclude_files:
            return False

        for pattern in self.exclude_files:
            # Para coincidencia directa (archivo o carpeta exacta)
            if path.match(pattern):
                return True

            # Para coincidencia recursiva (si una carpeta padre está excluida)
            for parent in path.parents:
                if str(parent) == ".":
                    break
                if parent.match(pattern):
                    return True
        return False

    def is_excluded_default(self, path: Path) -> bool:
        """Devuelve True si el archivo coincide con algún patrón de exclusión."""
        return any(path.match(pattern) for pattern in self.exclude_files_default)

    @field_validator("chat_id", mode="before")
    def convert_to_int_if_possible(cls, v):
        try:
            return int(v)
        except (ValueError, TypeError):
            return str(v)

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
