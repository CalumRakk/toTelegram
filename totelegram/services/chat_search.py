import logging
from typing import TYPE_CHECKING, Generator, List

from totelegram.core.schemas import ChatMatch, ChatResolution

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Chat


logger = logging.getLogger(__name__)


class ChatSearchService:
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

    def search_by_name(
        self, query: str, depth: int = 100, is_exact: bool = True
    ) -> ChatResolution:
        """
        Busca entre los chats recientes el chat con el nombre indicado. Si ``is_exact`` es True,
        se busca una coincidencia exacta.

        Args:
            query (str): Nombre del chat a buscar.
            is_exact (bool): Si es True, exige coincidencia exacta (case-sensitive).
                            Si es False, acepta coincidencia parcial (case-insensitive).
            depth (int): Número máximo de diálogos recientes a inspeccionar.

        Returns:
            ChatResolution: Estado final de la búsqueda y candidatos encontrados.
        """
        from pyrogram.types import Dialog

        query = query.strip()

        logger.debug(f"Resolviendo chat {query=} {is_exact=} {depth=}")

        result = ChatResolution(
            query=query, search_depth=depth, is_exact_requested=is_exact
        )

        candidates: List[ChatMatch] = []
        suggestions: List[ChatMatch] = []

        dialogs: Generator[Dialog] = self.client.get_dialogs(limit=depth)  # type: ignore

        for dialog in dialogs:
            title = dialog.chat.title or dialog.chat.first_name or ""
            match_data = ChatMatch.from_chat(dialog.chat)

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
