import enum
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, IntEnum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    cast,
)

from pydantic import BaseModel, Field

from totelegram import __version__
from totelegram.database import DatabaseSession
from totelegram.telegram.client import TelegramSession

if TYPE_CHECKING:
    from pyrogram.types import Chat

    from totelegram.identity import SettingsManager
    from totelegram.models import RemotePayload


MANIFEST_VERSION = "4.0"

APP_NAME = "toTelegram"
CLI_BIN = "totelegram"
VALUE_NOT_SET = "NOT-SET"
SELF_CHAT_ALIASES = ["me", "mensajes guardados", "saved messages", "self"]
ID_PREFIX_RE = re.compile(r"^id:", re.IGNORECASE)


class Commands:
    PROFILE_CREATE = f"{CLI_BIN} profile create"
    PROFILE_DELETE = f"{CLI_BIN} profile delete"
    CONFIG_SET = f"{CLI_BIN} config set"
    CONFIG_SEARCH = f"{CLI_BIN} config search"
    CONFIG_EDIT_LIST = f"{CLI_BIN} config add/remove"
    CONFIG_ADD_LIST = f"{CLI_BIN} config add"
    CONFIG_REMOVE_LIST = f"{CLI_BIN} config remove"
    PROFILE_SWITCH = f"{CLI_BIN} profile switch"


class COLORS:
    INFO = "dim cyan"
    WARNING = "magenta"
    ERROR = "bold red"
    SUCCESS = "bold green"
    PROGRESS = "italic blue"

    # bold blue para títulos de tablas
    TABLE_TITLE = "bold blue"


class Strategy(str, enum.Enum):
    SINGLE = "single-file"
    CHUNKED = "pieces-file"

    @classmethod
    def evaluate(cls, file_size: int, tg_limit: int) -> "Strategy":
        """Determina la estrategia de subida basado en el tamaño del archivo y el tamaño de Telegram."""
        return cls.SINGLE if file_size <= tg_limit else cls.CHUNKED


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    SPLITTED = "SPLITTED"
    UPLOADED = "UPLOADED"
    ORPHANED = "ORPHANED"


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


# --- SCHEMAS ---


class InfoField(BaseModel):
    level: AccessLevel
    field_name: str
    description: Optional[str]
    default_value: Any
    is_sensitive: bool
    type_annotation: str


class SourceMetadata(BaseModel):
    filename: str
    size: int
    md5sum: str
    mime_type: str
    mtime: float


class RemotePart(BaseModel):
    sequence: int
    message_id: int
    chat_id: int
    link: str
    part_filename: str
    part_size: int
    part_md5sum: str


class UploadManifest(BaseModel):
    version: str = MANIFEST_VERSION
    app_version: str
    created_at: datetime

    # Bloque de Identidad del Job
    strategy: Strategy
    chunk_size: int  # El 'tg_max_size' del contrato original (ADR-002)

    # Bloque de Identidad del Chat/Usuario
    target_chat_id: int
    owner_id: int
    owner_name: str

    source: SourceMetadata
    parts: List[RemotePart]


class StrategyConfig(BaseModel):
    # "Contrato de Partición".
    tg_max_size: int

    # Nos dice si el Job nació bajo el privilegio de una cuenta Premium.
    user_is_premium: bool

    app_version: str


class ProfileRegistry(BaseModel):
    """Modelo que representa el archivo config.json global de perfiles"""

    active: Optional[str] = None
    profiles: Dict[str, str] = Field(default_factory=dict)


class TapeCatalog(BaseModel):
    fingerprint: str
    total_size: int
    total_files: int
    created_at: float
    tartape_version: str
    exclude_patterns: str


@dataclass
class CLIState:
    manager: "SettingsManager"
    profile_name: Optional[str]
    is_debug: bool = False

    @contextmanager
    def scope(self):
        """Unifica el ciclo de vida de la DB y la Sesión."""
        profile_name = cast(str, self.manager.resolve_profile_name(self.profile_name))

        with DatabaseSession(self.manager.database_path) as db:
            with TelegramSession.from_profile(profile_name, self.manager) as client:
                yield client, db


class AccessStatus(str, Enum):
    READY = "ready"
    NOT_FOUND = "not_found"  # PEER_ID_INVALID
    NOT_MEMBER = "not_member"
    RESTRICTED = "restricted"  # Sin permisos de escritura


class ChatMatch(BaseModel):
    """Representación simplificada de un chat encontrado."""

    id: int
    title: str
    username: Optional[str] = None
    type: str

    @staticmethod
    def from_chat(chat: "Chat") -> "ChatMatch":
        return ChatMatch(
            id=chat.id,
            title=chat.title or chat.first_name or "Sin Titulo",
            username=chat.username,
            type=str(chat.type),
        )


class AccessReport(BaseModel):
    status: AccessStatus
    chat: Optional[ChatMatch] = None
    reason: str  # Mensaje técnico/explicativo
    hint: Optional[str] = None  # El "Tip" de UX para el usuario

    @property
    def is_ready(self) -> bool:
        return self.status == AccessStatus.READY


class ChatResolution(BaseModel):
    """Resultado estructurado del proceso de resolución de un chat."""

    query: str
    search_depth: int
    is_exact_requested: bool

    winner: Optional[ChatMatch] = Field(
        default=None,
        description="El chat que cumple estrictamente los criterios y no tiene rivales.",
    )
    conflicts: List[ChatMatch] = Field(
        default_factory=list,
        description="Chats que cumplen los criterios pero generan ambigüedad (ej. nombres duplicados).",
    )
    suggestions: List[ChatMatch] = Field(
        default_factory=list,
        description="Chats que no cumplen el criterio estricto pero son similares o parciales.",
    )

    @property
    def is_resolved(self) -> bool:
        """Indica si la búsqueda produjo un único resultado sin conflictos."""
        return self.winner is not None and len(self.conflicts) == 0

    @property
    def is_ambiguous(self) -> bool:
        """Indica si existen múltiples coincidencias para el mismo query."""
        return len(self.conflicts) > 1

    @property
    def needs_help(self) -> bool:
        """Indica si no hubo un ganador, pero existen sugerencias disponibles."""
        return self.winner is None and len(self.suggestions) > 0

    def all_unique_matches(self) -> List[ChatMatch]:
        """Devuelve una lista combinada de ganador, conflictos y sugerencias sin duplicados."""
        all_matches: List[ChatMatch] = []
        if self.winner:
            all_matches.append(self.winner)
        all_matches.extend(self.conflicts)
        all_matches.extend(self.suggestions)

        seen = set()
        unique_matches = []
        for m in all_matches:
            if m.id not in seen:
                seen.add(m.id)
                unique_matches.append(m)
        return unique_matches


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
        default_factory=list, description="Archivos validos para subir."
    )
    skipped_by_snapshot: list[Path] = Field(
        default_factory=list, description="Archivos que son un snapshot."
    )
    skipped_by_size: list[Path] = Field(
        default_factory=list, description="Archivos demasiado grandes."
    )
    skipped_by_exclusion: list[Path] = Field(
        default_factory=list,
        description="Archivos excluidos por patron de exclusión.",
    )
    exclusion_patterns: list[str] = Field(default_factory=list)

    @property
    def total_skipped(self) -> int:
        return (
            len(self.skipped_by_snapshot)
            + len(self.skipped_by_size)
            + len(self.skipped_by_exclusion)
        )

    @property
    def total_files(self) -> int:
        """Devuelve el total de archivos encontrados."""
        return len(self.found) + self.total_skipped

    @property
    def content_files(self) -> int:
        """Devuelve el total de archivos encontrados que no son snapshots."""
        return self.total_files - len(self.skipped_by_snapshot)
