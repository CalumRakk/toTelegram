import logging
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pyrogram.errors import FloodWait, RPCError

from totelegram.packaging.schemas import UploadManifest
from totelegram.telegram.captions import build_manifest_caption

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Message

logger = logging.getLogger(__name__)


class SnapshotPublicationError(Exception):
    """Lanzada cuando falla la publicación del snapshot en Telegram tras varios reintentos."""


class SnapshotPublisher:
    """Publica snapshots físicos en Telegram con reintentos automáticos y captions estructurados."""

    MAX_RETRIES = 3

    @classmethod
    def publish(
        cls,
        client: "Client",
        chat_id: int | str,
        snapshot_path: Path,
        manifest: UploadManifest,
    ) -> "Message":
        """Sube el archivo físico del snapshot (.json.xz o .ttape) al chat de destino.

        Args:
            client: Cliente de Pyrogram autenticado.
            chat_id: ID o alias del chat destino.
            snapshot_path: Ruta en disco del archivo .json.xz o .ttape a enviar.
            manifest: Manifiesto deserializado correspondiente.

        Returns:
            Message: El objeto Message confirmado por Telegram.

        Raises:
            SnapshotPublicationError: Si la subida falla tras agotar reintentos.
            FileNotFoundError: Si el archivo snapshot no existe en disco.
        """
        path = Path(snapshot_path)
        if not path.is_file():
            raise FileNotFoundError(f"Snapshot no encontrado en disco: {path}")

        # Extraer total_files si es un contenedor de carpeta (tape)
        total_files = None
        if manifest.source.tape_catalog:
            total_files = manifest.source.tape_catalog.total_files

        # Construir caption estructurado y seguro (< 1024 caracteres) con short_hash
        caption = build_manifest_caption(
            filename=manifest.source.filename,
            source_type=manifest.source.type,
            total_size=manifest.source.size,
            total_parts=len(manifest.parts),
            master_md5=manifest.source.md5sum[:32],
            total_files=total_files,
        )

        logger.info(
            f"Publicando snapshot '{path.name}' en chat {chat_id} "
            f"({len(manifest.parts)} partes, MD5: {manifest.source.md5sum[:8]}...)"
        )

        for attempt in range(1, cls.MAX_RETRIES + 1):
            try:
                from pyrogram.types import Message

                sent_message = cast(
                    Message,
                    client.send_document(
                        chat_id=chat_id,
                        document=str(path),
                        file_name=path.name,
                        caption=caption,
                        force_document=True,
                    ),
                )
                logger.info(
                    f"Snapshot '{path.name}' publicado con éxito en Telegram "
                    f"(Msg ID: {sent_message.id})"
                )
                return cast("Message", sent_message)

            except FloodWait as e:
                wait_seconds = int(e.value) + random.randint(2, 5)  # type: ignore
                logger.warning(
                    f"FloodWait al publicar snapshot (intento {attempt}/{cls.MAX_RETRIES}): "
                    f"Esperando {wait_seconds}s..."
                )
                time.sleep(wait_seconds)

            except RPCError as e:
                logger.error(
                    f"Error de Telegram enviando snapshot (intento {attempt}/{cls.MAX_RETRIES}): {e}"
                )
                if attempt == cls.MAX_RETRIES:
                    raise SnapshotPublicationError(
                        f"Fallo definitivo al publicar snapshot '{path.name}': {e}"
                    ) from e
                time.sleep(2)

        raise SnapshotPublicationError(
            f"No se pudo publicar el snapshot '{path.name}' tras {cls.MAX_RETRIES} intentos."
        )
