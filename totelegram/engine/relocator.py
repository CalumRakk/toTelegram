import json
import logging
from typing import TYPE_CHECKING, List, cast

import peewee

from totelegram.common.helpers import get_utc_now
from totelegram.database import db_transaction
from totelegram.exceptions import PermissionDeniedCleanupError
from totelegram.models import RemotePayload

if TYPE_CHECKING:
    from pyrogram.client import Client


logger = logging.getLogger(__name__)


class ChunkRelocationError(Exception):
    """Lanzada cuando ocurre un error irrecuperable al reubicar trozos en Telegram."""


class ChunkRelocator:
    """
    Servicio de reubicación atómica de trozos en Telegram (Anti-Waste TOC).
    Copia los mensajes existentes al fondo del chat mediante 'copy_message'
    y destruye los mensajes viejos para satisfacer la contigüidad sin gastar ancho de banda.
    """

    @classmethod
    def relocate(
        cls,
        client: "Client",
        db: peewee.Database,
        chat_id: int | str,
        remotes: List[RemotePayload],
    ) -> List[RemotePayload]:
        """
        Reubica una lista de RemotePayload al fondo del chat de destino.

        Args:
            client: Cliente autenticado de Pyrogram.
            db: Base de datos SQLite activa.
            chat_id: ID del chat donde residen las partes.
            remotes: Lista de RemotePayload a reubicar.

        Returns:
            List[RemotePayload]: Registros actualizados en base de datos.

        Raises:
            PermissionDeniedCleanupError: Si no se pueden borrar los mensajes viejos.
            ChunkRelocationError: Si falla la copia en los servidores de Telegram.
        """
        if not remotes:
            return []

        # Ordenar estrictamente por secuencia para que queden consecutivos en Telegram
        sorted_remotes = sorted(remotes, key=lambda r: r.payload.sequence_index)
        copied_records = []

        logger.info(
            f"Iniciando reubicación atómica de {len(sorted_remotes)} trozos en chat {chat_id}..."
        )

        # Copiar mensajes secuencialmente al fondo del chat
        try:
            for r in sorted_remotes:
                from pyrogram.types import Message

                new_msg = cast(
                    Message,
                    client.copy_message(
                        chat_id=chat_id,
                        from_chat_id=chat_id,
                        message_id=r.message_id,
                    ),
                )
                copied_records.append((r, new_msg))
                logger.debug(
                    f"Trozo seq {r.payload.sequence_index} copiado: Msg {r.message_id} -> {new_msg.id}"
                )
        except Exception as e:
            # Rollback: destruir copias recién creadas para no dejar duplicados
            cls._rollback_messages(
                client, chat_id, [msg.id for _, msg in copied_records]
            )
            raise ChunkRelocationError(
                f"Fallo al copiar trozos al fondo del canal: {e}"
            ) from e

        # Eliminar mensajes antiguos de Telegram
        old_message_ids = [r.message_id for r in sorted_remotes]
        try:
            client.delete_messages(chat_id=chat_id, message_ids=old_message_ids)
            logger.info(
                f"Mensajes antiguos eliminados exitosamente en Telegram: {old_message_ids}"
            )
        except Exception as e:
            # Si falla el borrado, deshacemos las copias para no dejar basura duplicada
            cls._rollback_messages(
                client, chat_id, [msg.id for _, msg in copied_records]
            )
            raise PermissionDeniedCleanupError(
                chat_id=chat_id,
                reason=f"No se pudieron eliminar los trozos antiguos ({old_message_ids}): {e}",
            ) from e

        # Actualizar la base de datos local en una transacción atómica
        with db_transaction(db):
            for remote_obj, new_msg in copied_records:
                remote_obj.message_id = new_msg.id
                remote_obj.json_metadata = json.loads(str(new_msg))
                remote_obj.last_verified_at = get_utc_now()
                remote_obj.is_orphaned = False
                remote_obj.save(
                    only=[
                        RemotePayload.message_id,
                        RemotePayload.json_metadata,
                        RemotePayload.last_verified_at,
                        RemotePayload.is_orphaned,
                        RemotePayload.updated_at,
                    ]
                )

        logger.info(
            f"Reubicación completada: {len(copied_records)} trozos reposicionados al fondo del chat."
        )
        return [r for r, _ in copied_records]

    @staticmethod
    def _rollback_messages(
        client: "Client", chat_id: int | str, message_ids: List[int]
    ) -> None:
        """Intenta eliminar mensajes provisionales en caso de fallo."""
        if not message_ids:
            return
        try:
            client.delete_messages(chat_id=chat_id, message_ids=message_ids)
        except Exception as e:
            logger.warning(
                f"Aviso durante rollback de mensajes en chat {chat_id} ({message_ids}): {e}"
            )
