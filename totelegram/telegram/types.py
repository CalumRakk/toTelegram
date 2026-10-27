from enum import Enum
from typing import TYPE_CHECKING, List

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
    username: str | None = None
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
    chat: ChatMatch | None = None
    reason: str  # Mensaje técnico/explicativo
    hint: str | None = None  # El "Tip" de UX para el usuario

    @property
    def is_ready(self) -> bool:
        return self.status == AccessStatus.READY


class ChatResolution(BaseModel):
    """Resultado estructurado del proceso de resolución de un chat."""

    query: str
    search_depth: int
    is_exact_requested: bool

    winner: ChatMatch | None = Field(
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


class TargetResolutionStatus(str, Enum):
    RESOLVED = "resolved"  # Chat encontrado y con permisos de escritura confirmados
    AMBIGUOUS = "ambiguous"  # Múltiples chats coinciden con la búsqueda
    NOT_FOUND = "not_found"  # No se encontró ningún chat
    NO_PERMISSIONS = (
        "no_permissions"  # Encontrado, pero sin permisos o el usuario no es miembro
    )
    INVALID_INPUT = "invalid_input"  # Entrada vacía o sentinel NOT_SET


class TargetResolution(BaseModel):
    """Resultado unificado de la resolución de un destino (Target)."""

    status: TargetResolutionStatus
    query: str
    chat_id: int | str | None = None
    title: str | None = None
    match: ChatMatch | None = None
    candidates: List[ChatMatch] = Field(default_factory=list)
    error_message: str | None = None
    hint: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.status == TargetResolutionStatus.RESOLVED

    @property
    def is_ambiguous(self) -> bool:
        return self.status == TargetResolutionStatus.AMBIGUOUS
