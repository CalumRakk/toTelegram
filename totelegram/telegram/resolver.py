import logging
from typing import TYPE_CHECKING

import peewee

from totelegram.models import TelegramChat
from totelegram.schemas import VALUE_NOT_SET
from totelegram.telegram.access import ChatAccessService
from totelegram.telegram.search import ChatSearchService
from totelegram.telegram.types import (
    AccessStatus,
    ChatMatch,
    TargetResolution,
    TargetResolutionStatus,
)
from totelegram.telegram.utils import (
    is_direct_identifier,
    normalize_chat_id,
    normalize_text_for_search,
)

if TYPE_CHECKING:
    from pyrogram.client import Client

logger = logging.getLogger(__name__)


class TargetResolver:
    """
    Resolvedor de destinos en 3 niveles de velocidad:
    1. Directo: IDs numéricos, 'me', @usernames y links (O(1), 0 ms de red).
    2. Caché SQLite: Coincidencias previas verificadas en TelegramChat (O(1), <1 ms).
    3. Telegram API: Búsqueda exhaustiva en diálogos con Exact Match Precedence.
    """

    def __init__(
        self,
        client: "Client",
        db: peewee.Database | None = None,
    ):
        self.client = client
        self.db = db
        self.access_service = ChatAccessService(client)
        self.search_service = ChatSearchService(client)

    def resolve(
        self,
        query: str | int | None,
        depth: int = 100,
    ) -> TargetResolution:
        raw_query = "" if query is None else str(query).strip()

        if not raw_query or raw_query.upper() == VALUE_NOT_SET:
            return TargetResolution(
                status=TargetResolutionStatus.INVALID_INPUT,
                query=raw_query,
                error_message="No se proporcionó un destino.",
                hint="Especifica el destino usando '-t <chat>' (ej: '-t me' o '-t \"Mi Canal\"').",
            )

        # Nivel 1: Identificador Directo O(1)
        if is_direct_identifier(raw_query):
            logger.debug(f"[TargetResolver] Nivel 1 (Directo): {raw_query}")
            return self._resolve_direct(raw_query)

        # Nivel 2: Caché Local SQLite O(1)
        cached_resolution = self._resolve_from_cache(raw_query)
        if cached_resolution is not None:
            logger.debug(f"[TargetResolver] Nivel 2 (Caché SQLite): {raw_query}")
            return cached_resolution

        # Nivel 3: Búsqueda en vivo en Telegram API
        logger.debug(f"[TargetResolver] Nivel 3 (API Telegram): {raw_query}")
        return self._resolve_from_telegram_search(raw_query, depth=depth)

    def _resolve_direct(self, raw_query: str) -> TargetResolution:
        """Resuelve identificadores directos validando acceso y compatibilidad de tipo."""
        try:
            normalized_id = normalize_chat_id(raw_query)
        except ValueError as e:
            return TargetResolution(
                status=TargetResolutionStatus.INVALID_INPUT,
                query=raw_query,
                error_message=str(e),
            )

        access_report = self.access_service.verify_access(normalized_id)

        if access_report.is_ready and access_report.chat:
            self._cache_chat_if_possible(access_report.chat)
            return TargetResolution(
                status=TargetResolutionStatus.RESOLVED,
                query=raw_query,
                chat_id=access_report.chat.id,
                title=access_report.chat.title,
                match=access_report.chat,
            )

        if access_report.status == AccessStatus.NOT_FOUND:
            return TargetResolution(
                status=TargetResolutionStatus.NOT_FOUND,
                query=raw_query,
                error_message=access_report.reason,
                hint=access_report.hint,
            )

        if access_report.status == AccessStatus.UNSUPPORTED_TYPE:
            return TargetResolution(
                status=TargetResolutionStatus.UNSUPPORTED_TYPE,
                query=raw_query,
                chat_id=access_report.chat.id if access_report.chat else None,
                title=access_report.chat.title if access_report.chat else None,
                match=access_report.chat,
                error_message=access_report.reason,
                hint=access_report.hint,
            )

        return TargetResolution(
            status=TargetResolutionStatus.NO_PERMISSIONS,
            query=raw_query,
            chat_id=access_report.chat.id if access_report.chat else None,
            title=access_report.chat.title if access_report.chat else None,
            match=access_report.chat,
            error_message=access_report.reason,
            hint=access_report.hint,
        )

    def _resolve_from_cache(self, query: str) -> TargetResolution | None:
        """Busca en SQLite si el canal ya fue verificado anteriormente, ignorando grupos legacy."""
        from totelegram.database import db_proxy

        active_db = self.db or (
            db_proxy.obj
            if (db_proxy.obj is not None and not db_proxy.is_closed())
            else None
        )
        if not active_db:
            return None

        norm_query = normalize_text_for_search(query)

        try:
            # Traer solo canales conocidos en local (ignorar grupos heredados en BD)
            all_cached = list(
                TelegramChat.select().where(TelegramChat.type == "channel")
            )
            exact_cached = [
                c
                for c in all_cached
                if normalize_text_for_search(c.title) == norm_query
                or (c.username and normalize_text_for_search(c.username) == norm_query)
            ]

            # Si hay una única coincidencia exacta en caché, validamos que siga viva y accesible
            if len(exact_cached) == 1:
                cached_chat = exact_cached[0]
                access_report = self.access_service.verify_access(cached_chat.id)
                if access_report.is_ready and access_report.chat:
                    return TargetResolution(
                        status=TargetResolutionStatus.RESOLVED,
                        query=query,
                        chat_id=access_report.chat.id,
                        title=access_report.chat.title,
                        match=access_report.chat,
                    )
        except Exception as e:
            logger.debug(f"Aviso consultando caché de canales en SQLite: {e}")

        return None

    def _resolve_from_telegram_search(self, query: str, depth: int) -> TargetResolution:
        """Busca en diálogos recientes aplicando la regla Exact Match Precedence entre canales."""
        clean_query = query.strip()
        norm_query = normalize_text_for_search(clean_query)

        search_result = self.search_service.search_by_name(
            clean_query, depth=depth, is_exact=False
        )
        all_unique = search_result.all_unique_matches()

        if not all_unique:
            return TargetResolution(
                status=TargetResolutionStatus.NOT_FOUND,
                query=query,
                error_message=f"No se encontró ningún canal que coincida con '{query}'.",
                hint="Tip: Asegúrate de ser administrador del canal, o usa su ID numérico directo.",
            )

        exact_matches = [
            m
            for m in all_unique
            if normalize_text_for_search(m.title) == norm_query
            or (m.username and normalize_text_for_search(m.username) == norm_query)
        ]

        if len(exact_matches) == 1:
            return self._verify_and_build(exact_matches[0], query)

        if len(exact_matches) > 1:
            return TargetResolution(
                status=TargetResolutionStatus.AMBIGUOUS,
                query=query,
                candidates=exact_matches,
                error_message=f"Se encontraron múltiples canales llamados exactamente '{query}'.",
                hint="Especifica el ID directo (ej: -t -100xxxxxxxxxx) para desempatar.",
            )

        if len(all_unique) == 1:
            return self._verify_and_build(all_unique[0], query)

        return TargetResolution(
            status=TargetResolutionStatus.AMBIGUOUS,
            query=query,
            candidates=all_unique,
            error_message=f"El nombre '{query}' es ambiguo ({len(all_unique)} canales coincidentes).",
            hint="Especifica el ID directo de uno de los candidatos mostrados.",
        )

    def _verify_and_build(
        self, candidate: ChatMatch, original_query: str
    ) -> TargetResolution:
        """Verifica permisos de publicación y construye el resultado de resolución."""
        access_report = self.access_service.verify_access(candidate.id)

        if access_report.is_ready and access_report.chat:
            self._cache_chat_if_possible(access_report.chat)
            return TargetResolution(
                status=TargetResolutionStatus.RESOLVED,
                query=original_query,
                chat_id=access_report.chat.id,
                title=access_report.chat.title,
                match=access_report.chat,
            )

        if access_report.status == AccessStatus.UNSUPPORTED_TYPE:
            status = TargetResolutionStatus.UNSUPPORTED_TYPE
        elif access_report.status == AccessStatus.NOT_FOUND:
            status = TargetResolutionStatus.NOT_FOUND
        else:
            status = TargetResolutionStatus.NO_PERMISSIONS

        return TargetResolution(
            status=status,
            query=original_query,
            chat_id=candidate.id,
            title=candidate.title,
            match=candidate,
            error_message=access_report.reason,
            hint=access_report.hint,
        )

    def _cache_chat_if_possible(self, chat: ChatMatch) -> None:
        """Guarda o actualiza el chat en SQLite para acelerar próximas resoluciones a 0 ms."""
        from totelegram.database import db_proxy

        active_db = self.db or (
            db_proxy.obj
            if (db_proxy.obj is not None and not db_proxy.is_closed())
            else None
        )
        if not active_db:
            return

        try:
            from datetime import datetime, timezone

            with active_db.atomic():
                chat_record, created = TelegramChat.get_or_create(
                    id=chat.id,
                    defaults={
                        "title": chat.title,
                        "username": chat.username,
                        "type": chat.type,
                        "is_public": bool(chat.username),
                        "last_verified": datetime.now(timezone.utc),
                    },
                )
                if not created:
                    chat_record.title = chat.title
                    chat_record.username = chat.username
                    chat_record.type = chat.type
                    chat_record.is_public = bool(chat.username)
                    chat_record.last_verified = datetime.now(timezone.utc)
                    chat_record.save()
        except Exception as e:
            logger.debug(f"Aviso actualizando chat en BD local: {e}")
