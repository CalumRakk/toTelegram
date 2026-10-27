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
)

if TYPE_CHECKING:
    from pyrogram.client import Client

logger = logging.getLogger(__name__)


class TargetResolver:
    """
    Resolvedor de destinos en 3 niveles de velocidad:
    1. Directo: IDs numéricos, 'me', @usernames y links.
    2. Caché SQLite: Coincidencias previas en la tabla TelegramChat.
    3. Telegram API: Búsqueda exhaustiva en diálogos con discriminación anti-ambigüedad.
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
        """
        Resuelve una consulta de destino a un chat de Telegram verificado.

        Args:
            query: Identificador o nombre ('me', '-100...', '@canal', 'Respaldos').
            depth: Profundidad de búsqueda de diálogos en la API de Telegram.

        Returns:
            TargetResolution: DTO con el estado, el chat resuelto y candidatos si es ambiguo.
        """
        raw_query = "" if query is None else str(query).strip()

        # Validación de entrada
        if not raw_query or raw_query.upper() == VALUE_NOT_SET:
            return TargetResolution(
                status=TargetResolutionStatus.INVALID_INPUT,
                query=raw_query,
                error_message="No se proporcionó un destino válido.",
                hint="Especifica un destino usando --target <chat> o configura uno global con 'totelegram target <chat>'.",
            )

        # Identificador Directo O(1)
        # (IDs numéricos, 'me', alias de guardados, @usernames, links)

        if is_direct_identifier(raw_query):
            logger.debug(f"[TargetResolver] Nivel 1 (Directo): {raw_query}")
            return self._resolve_direct(raw_query)

        # Caché Local SQLite O(1)
        # (Búsqueda en la tabla TelegramChat de chats previamente validados)

        cached_resolution = self._resolve_from_cache(raw_query)
        if cached_resolution is not None:
            logger.debug(f"[TargetResolver] Nivel 2 (Caché SQLite): {raw_query}")
            return cached_resolution

        # Búsqueda en vivo en Telegram API
        # (Escaneo de diálogos con filtro estricto de ambigüedad)

        logger.debug(f"[TargetResolver] Nivel 3 (API Telegram): {raw_query}")
        return self._resolve_from_telegram_search(raw_query, depth=depth)

    def _resolve_direct(self, raw_query: str) -> TargetResolution:
        """Resuelve identificadores directos validando acceso y permisos."""
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
        """
        Intenta resolver consultando la base de datos local.
        Retorna TargetResolution si encuentra una coincidencia exacta y única verificada,
        o None si no existe o hay dudas para que el Nivel 3 se encargue.
        """
        from totelegram.database import db_proxy

        active_db = self.db or (
            db_proxy.obj
            if (db_proxy.obj is not None and not db_proxy.is_closed())
            else None
        )
        if not active_db:
            return None

        clean_query = query.strip().lower()

        try:
            # Buscar coincidencia exacta por título o username (case-insensitive)
            matches = list(
                TelegramChat.select().where(
                    (peewee.fn.LOWER(TelegramChat.title) == clean_query)
                    | (peewee.fn.LOWER(TelegramChat.username) == clean_query)
                )
            )

            # Si hay más de un chat en local con el mismo nombre, es ambiguo en caché
            if len(matches) != 1:
                return None

            cached_chat = matches[0]

            # Verificación JIT de que el chat de la caché siga existiendo y tengamos permisos
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
            logger.debug(f"Aviso consultando caché de chats en SQLite: {e}")

        return None

    def _resolve_from_telegram_search(self, query: str, depth: int) -> TargetResolution:
        """Busca en los diálogos recientes de Telegram aplicando filtros de ambigüedad."""
        clean_query = query.strip()
        search_result = self.search_service.search_by_name(
            clean_query, depth=depth, is_exact=False
        )
        all_unique = search_result.all_unique_matches()

        if not all_unique:
            return TargetResolution(
                status=TargetResolutionStatus.NOT_FOUND,
                query=query,
                error_message=f"No se encontró ningún chat que coincida con '{query}'.",
                hint="Tip: Envía un mensaje en el chat desde tu app de Telegram para que aparezca en los diálogos recientes.",
            )

        # Prioridad: Coincidencias exactas por título (case-insensitive)
        exact_matches = [
            m for m in all_unique if m.title.strip().lower() == clean_query.lower()
        ]

        if len(exact_matches) == 1:
            return self._verify_and_build(exact_matches[0], query)

        if len(exact_matches) > 1:
            return TargetResolution(
                status=TargetResolutionStatus.AMBIGUOUS,
                query=query,
                candidates=exact_matches,
                error_message=f"Se encontraron múltiples chats llamados exactamente '{query}'.",
                hint="Especifica el ID directo (ej: --target -100xxxxxxxxxx) para desempatar.",
            )

        # Si no hubo coincidencia exacta, evaluamos coincidencias parciales
        if len(all_unique) == 1:
            return self._verify_and_build(all_unique[0], query)

        # Si hay múltiples coincidencias parciales -> Ambiguo
        return TargetResolution(
            status=TargetResolutionStatus.AMBIGUOUS,
            query=query,
            candidates=all_unique,
            error_message=f"El nombre '{query}' es ambiguo. Se encontraron {len(all_unique)} coincidencias.",
            hint="Usa el ID directo de uno de los candidatos mostrados.",
        )

    def _verify_and_build(
        self, candidate: ChatMatch, original_query: str
    ) -> TargetResolution:
        """Verifica permisos del candidato ganador y construye el resultado."""
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

        status = (
            TargetResolutionStatus.NOT_FOUND
            if access_report.status == AccessStatus.NOT_FOUND
            else TargetResolutionStatus.NO_PERMISSIONS
        )

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
        """Guarda o actualiza el chat en la base de datos local SQLite si está disponible."""
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
