from enum import Enum
from typing import (
    TYPE_CHECKING,
    List,
    Optional,
)

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pyrogram.types import Chat


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
