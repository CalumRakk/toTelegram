import logging
from typing import TYPE_CHECKING, Generator, List, Optional, cast

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Chat

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ChatMatch(BaseModel):
    """Representación simplificada de un chat encontrado."""

    id: int
    title: str
    username: Optional[str] = None
    type: str


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


class ChatResolverService:
    def __init__(self, client: "Client"):
        self.client = client

    def is_direct(self, query: int | str) -> bool:
        """Identifica si el query es un chat directo como ID, @username o t.me/username."""
        query = str(query)
        return (
            query.startswith("@")
            or "t.me/" in query
            or query.replace("-", "").isdigit()
        )

    def resolve(
        self, query: str, is_exact: bool = True, depth: int = 100
    ) -> ChatResolution:
        """
        Resuelve un identificador textual en un chat de Telegram aplicando reglas de búsqueda.

        El proceso consta de tres etapas:
        1. Resolución directa: si el query es un ID, @username o enlace, se consulta la API.
        2. Exploración de diálogos: se inspeccionan los chats recientes del usuario.
        3. Clasificación: los resultados se organizan en ganador, conflictos o sugerencias.

        Args:
            query (str): Nombre, username, ID o enlace del chat a buscar.
            is_exact (bool): Si es True, exige coincidencia exacta (case-sensitive).
                            Si es False, acepta coincidencia parcial (case-insensitive).
            depth (int): Número máximo de diálogos recientes a inspeccionar.

        Returns:
            ChatResolution: Estado final de la búsqueda y candidatos encontrados.
        """
        from pyrogram.types import Chat, Dialog

        query = query.strip()

        logger.debug(f"Resolviendo chat {query=} {is_exact=} {depth=}")

        result = ChatResolution(
            query=query, search_depth=depth, is_exact_requested=is_exact
        )

        if self.is_direct(query):
            chat = cast(Chat, self.client.get_chat(query))
            result.winner = self._to_item(chat)
            return result
        else:
            candidates: List[ChatMatch] = []
            suggestions: List[ChatMatch] = []

            dialogs: Generator[Dialog] = self.client.get_dialogs(limit=depth)  # type: ignore

            for dialog in dialogs:
                title = dialog.chat.title or dialog.chat.first_name or ""
                match_data = self._to_item(dialog.chat)

                if is_exact:
                    if title == query:
                        candidates.append(match_data)
                    elif query.lower() in title.lower():
                        # No es exacto, pero se parece.
                        suggestions.append(match_data)
                else:
                    if query.lower() in title.lower():
                        candidates.append(match_data)

            # Clasificacion de resultado.
            if len(candidates) == 1:
                result.winner = candidates[0]
                logger.info(f"Chat resuelto exitosamente {query=} {result.winner.id=}")

            elif len(candidates) > 1:
                result.conflicts = candidates
                logger.info(
                    f"Resolución ambigua  {query=} conflictos={len(result.conflicts)}"
                )

            # Las sugerencias solo se llenan si no hubo un ganador claro
            if not result.winner:
                result.suggestions = suggestions
                logger.debug(
                    f"Sin coincidencia exacta {query=} sugerencias={len(result.suggestions)}"
                )

            return result

    def _to_item(self, chat: "Chat") -> ChatMatch:
        return ChatMatch(
            id=chat.id,
            title=chat.title or chat.first_name or "Sin Titulo",
            username=chat.username,
            type=str(chat.type),
        )
