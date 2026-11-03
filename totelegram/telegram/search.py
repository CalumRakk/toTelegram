# pyright: reportGeneralTypeIssues=false
import logging
from typing import TYPE_CHECKING, Generator

from totelegram.telegram.types import ChatMatch, ChatResolution
from totelegram.telegram.utils import normalize_text_for_search

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Message


logger = logging.getLogger(__name__)


def find_messages_with_history_fallback(
    client: "Client",
    chat_id: int | str,
    query: str,
    search_limit: int = 25,
    history_limit: int = 5,
) -> list["Message"]:
    """
    Busca mensajes combinando el motor de búsqueda de Telegram con una
    inspección del historial reciente para evitar falsos negativos por
    latencia de indexación asíncrona.
    """
    from pyrogram.errors import RPCError

    found_by_id: dict[int, "Message"] = {}

    try:
        for msg in client.search_messages(chat_id, query=query, limit=search_limit):
            if msg and msg.id:
                found_by_id[msg.id] = msg
    except RPCError as e:
        logger.warning(f"Error en búsqueda indexada en {chat_id}: {e}")

    try:
        for msg in client.get_chat_history(chat_id, limit=history_limit):
            if not msg or not msg.text:
                continue
            if query.lower() in msg.text.lower():
                found_by_id[msg.id] = msg
    except RPCError as e:
        logger.warning(f"Error consultando historial reciente en {chat_id}: {e}")

    return sorted(found_by_id.values(), key=lambda m: m.id)


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
        from pyrogram.enums import ChatType
        from pyrogram.types import Dialog

        clean_query = query.strip()
        norm_query = normalize_text_for_search(clean_query)

        logger.debug(f"Buscando canales coincidentes {query=} {is_exact=} {depth=}")

        result = ChatResolution(
            query=clean_query, search_depth=depth, is_exact_requested=is_exact
        )

        candidates: list[ChatMatch] = []
        suggestions: list[ChatMatch] = []

        dialogs: Generator[Dialog] = self.client.get_dialogs(limit=depth)  # type: ignore

        for dialog in dialogs:
            # Filtro temprano: solo considerar Canales de difusión
            chat_type = dialog.chat.type
            is_channel = (
                chat_type == ChatType.CHANNEL
                or getattr(chat_type, "value", None) == "channel"
                or str(chat_type).lower() in ("chattype.channel", "channel")
            )
            if not is_channel:
                continue

            title = dialog.chat.title or ""
            norm_title = normalize_text_for_search(title)
            username = dialog.chat.username or ""
            norm_username = normalize_text_for_search(username)

            match_data = ChatMatch.from_chat(dialog.chat)

            is_match_exact = (norm_title == norm_query) or (norm_username == norm_query)
            is_match_partial = (norm_query in norm_title) or (
                norm_query in norm_username
            )

            if is_exact:
                if is_match_exact:
                    candidates.append(match_data)
                elif is_match_partial:
                    suggestions.append(match_data)
            else:
                if is_match_exact:
                    candidates.insert(0, match_data)  # Exactos primero
                elif is_match_partial:
                    candidates.append(match_data)

        if len(candidates) == 1:
            result.winner = candidates[0]
            logger.info(f"Canal resuelto exitosamente {query=} {result.winner.id=}")

        elif len(candidates) > 1:
            result.conflicts = candidates
            logger.info(
                f"Resolución con múltiples canales candidatos {query=} conflictos={len(result.conflicts)}"
            )

        if not result.winner:
            result.suggestions = suggestions

        return result
