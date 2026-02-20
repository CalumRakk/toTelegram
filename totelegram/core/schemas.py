from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from pyrogram.types import Chat

from pydantic import BaseModel, Field

from totelegram import __version__
from totelegram.core.enums import Strategy
from totelegram.core.registry import SettingsManager

MANIFEST_VERSION = "4.0"


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


class Inventory(BaseModel):
    fingerprint: str
    total_size: int
    total_files: int
    scan_date: float
    scan_version: str
    db_path: str


class CLIState(BaseModel):
    manager: "SettingsManager"
    settings_name: Optional[str]
    is_debug: bool = False

    model_config = {"arbitrary_types_allowed": True}


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
