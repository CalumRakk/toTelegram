import enum
import re
import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Dict, Literal

from pydantic import BaseModel, Field

APP_NAME = "toTelegram"
CLI_BIN = "totelegram"
VALUE_NOT_SET = "NOT-SET"
SELF_CHAT_ALIASES = ["me", "mensajes guardados", "saved messages", "self"]
ID_PREFIX_RE = re.compile(r"^id:", re.IGNORECASE)

# Claves que apuntan a servicios externos y requieren verificación de red activa
NETWORK_FIELDS = {"chat_id"}


class ExecutionDecision(str, enum.Enum):
    """Decisión de ejecución resuelta por el motor para un Job en su chat destino."""

    ALREADY_FULFILLED = "already_fulfilled"
    # El archivo ya está 100% en el canal (Snapshot verificado JIT). No transfiere bytes.

    SEAL_HEALED = "seal_healed"
    # Todos los trozos físicos (N/N) están en Telegram, pero faltaba el snapshot.
    # Se genera y publica el snapshot sin transferir datos de archivo.

    RESUMED_IN_PLACE = "resumed_in_place"
    # Reanudación estándar sin --force. Respeta las partes ya subidas y continúa
    # subiendo las partes faltantes desde el último offset.

    RELOCATED_AND_RESUMED = "relocated_and_resumed"
    # Reanudación con --force y corte compatible (Anti-Waste TOC).
    # Reubica las partes viejas al fondo del chat con copy_message, borra las viejas
    # y sube las partes restantes de forma consecutiva.

    CLEAN_RESTART = "clean_restart"
    # Reinicio limpio con --force ante cambio de corte incompatible (ej. 4GB -> 2GB).
    # Elimina trozos viejos en Telegram, purga el Job previo y sube desde cero.

    INCOMPATIBLE_CAPACITY = "incompatible_capacity"
    # Bloqueo: El Job previo exige una capacidad mayor a la que la sesión activa posee
    # (ej. Job de 4 GB iniciado con Premium, ejecutado hoy por cuenta normal de 2 GB).

    PERMISSION_DENIED_CLEANUP = "permission_denied_cleanup"
    # Bloqueo: Se requiere limpiar o reubicar mensajes en Telegram, pero la cuenta
    # carece del permiso de administrador 'can_delete_messages'.


class Commands:
    AUTH_LOGIN = f"{CLI_BIN} login"
    AUTH_LOGOUT = f"{CLI_BIN} logout"
    AUTH_SWITCH = f"{CLI_BIN} switch"
    TARGET = f"{CLI_BIN} target"
    CONFIG_SET = f"{CLI_BIN} config set"
    CONFIG_EDIT_LIST = f"{CLI_BIN} config add/remove"
    CONFIG_ADD_LIST = f"{CLI_BIN} config add"
    CONFIG_REMOVE_LIST = f"{CLI_BIN} config remove"
    CLI_BIN = None


class COLORS:
    INFO = "dim cyan"
    WARNING = "magenta"
    ERROR = "bold red"
    SUCCESS = "bold green"
    PROGRESS = "italic blue"

    # bold blue para títulos de tablas
    TABLE_TITLE = "bold blue"


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    UPLOADED = "UPLOADED"
    DELETED = "DELETED"


class AvailabilityState(str, enum.Enum):
    FULFILLED = "fulfilled"
    CAN_FORWARD = "can-forward"
    NEEDS_UPLOAD = "needs-upload"


class SourceType(str, enum.Enum):
    FILE = "file"
    FOLDER = "folder"


class AccessLevel(IntEnum):
    EDITABLE = 1  # Visible y editable
    DEBUG_EDITABLE = 2  # Visible y editable en DEBUG
    DEBUG_READONLY = 3  # Visible en DEBUG (solo lectura)


class InfoField(BaseModel):
    level: AccessLevel
    field_name: str
    description: str | None
    default_value: Any
    is_sensitive: bool
    type_annotation: str


class ProfileRegistry(BaseModel):
    """Modelo que representa el archivo config.json global de perfiles"""

    active: str | None = None
    profiles: Dict[str, str] = Field(default_factory=dict)


class TapeCatalog(BaseModel):
    fingerprint: str
    total_size: int
    total_files: int
    created_at: float
    tartape_version: str
    exclude_patterns: str


class IntentType(str, Enum):
    DIRECT_ID = "direct_id"
    DIRECT_USERNAME = "direct_username"
    DIRECT_LINK = "direct_link"
    DIRECT_ALIAS = "direct_alias"
    SEARCH_QUERY = "search_query"

    @property
    def is_direct(self) -> bool:
        return self in (
            IntentType.DIRECT_ID,
            IntentType.DIRECT_USERNAME,
            IntentType.DIRECT_LINK,
            IntentType.DIRECT_ALIAS,
        )

    @property
    def is_search(self) -> bool:
        return self == IntentType.SEARCH_QUERY


class ScanReport(BaseModel):
    found: list[Path] = Field(
        default_factory=list, description="Archivos o carpetas validos para subir."
    )

    skipped_by_snapshot: list[Path] = Field(
        default_factory=list, description="Archivos o carpetas que tienen un snapshot."
    )
    skipped_by_size: list[Path] = Field(
        default_factory=list, description="Archivos demasiado grandes."
    )
    skipped_by_exclusion: list[Path] = Field(
        default_factory=list,
        description="Archivos o carpetas excluidos por patron de exclusión.",
    )
    skipped_by_error: list[Path] = Field(
        default_factory=list,
        description="Archivos o carpetas ilegibles, con errores de permiso o corruptos.",
    )
    skipped_by_empty: list[Path] = Field(
        default_factory=list,
        description="Carpetas que no contienen archivos válidos para procesar.",
    )
    skipped_by_integrity: list[Path] = Field(
        default_factory=list,
        description="Carpetas que tartape las marca con error de integridad.",
    )

    exclusion_patterns: list[str] = Field(default_factory=list)

    @property
    def total_skipped(self) -> int:
        return (
            len(self.skipped_by_snapshot)
            + len(self.skipped_by_size)
            + len(self.skipped_by_exclusion)
            + len(self.skipped_by_error)
            + len(self.skipped_by_empty)
            + len(self.skipped_by_integrity)
        )

    @property
    def total_files(self) -> int:
        """Devuelve el total de archivos encontrados."""
        return len(self.found) + self.total_skipped

    @property
    def content_files(self) -> int:
        """Devuelve el total de archivos encontrados que no son snapshots."""
        return self.total_files - len(self.skipped_by_snapshot)

    def log_skip(
        self,
        path: Path,
        reason: Literal["snapshot", "size", "exclusion", "error", "empty", "integrity"],
    ):
        """Helper centralizado para registrar omisiones."""
        mapping = {
            "snapshot": self.skipped_by_snapshot,
            "size": self.skipped_by_size,
            "exclusion": self.skipped_by_exclusion,
            "error": self.skipped_by_error,
            "empty": self.skipped_by_empty,
            "integrity": self.skipped_by_integrity,
        }
        mapping[reason].append(path)


@dataclass
class ProgressState:
    """Objeto para comunicar estados extra desde los parches a la UI y controlar el throttling de renovación."""

    status: str = "[blue]Subiendo...[/]"
    # Registra el tiempo de inicio
    last_renew: float = field(default_factory=time.monotonic)
    # Intervalo de renovación de lease (2 minutos por defecto)
    renew_interval_seconds: int = 120


class ResourceType(str, Enum):
    ACCOUNT = "account"  # Exclusividad de cuenta de Telegram (account:12345)
    PAYLOAD = "payload"  # Exclusividad de la pieza física en subida (payload:67890)
