import logging
from pathlib import Path
from typing import Optional, Tuple

from totelegram.core.enums import Strategy
from totelegram.core.setting import Settings
from totelegram.store.models import Payload, RemotePayload
from totelegram.streams import open_upload_source

logger = logging.getLogger(__name__)


class UploadProgress:
    """
    Maneja el estado del progreso de subida para un archivo específico.
    Actúa como un 'callable' para ser compatible con Pyrogram.
    """

    def __init__(self, filename: str):
        self.filename = filename
        self.last_percentage = -1

    def __call__(self, current: int, total: int):
        percentage = int(current * 100 / total)

        # Loguear solo si cambia el porcentaje y es múltiplo de 5
        if percentage % 5 == 0 and self.last_percentage != percentage:
            self.last_percentage = percentage
            logger.info(
                f"Subiendo {self.filename}: {current} de {total} bytes ({percentage}%)"
            )


class UploadService:
    # TODO : encontrar un truco elegante para especificar el tipo de client de telegram
    def __init__(self, client, settings: Settings):
        self.client = client
        self.settings = settings

    def upload_payload(self, payload: Payload) -> Optional[RemotePayload]:
        """
        Sube un Payload específico a Telegram.
        Si ya existe un RemotePayload asociado, omite la subida.
        """
        # Verificamos si ya se subió previamente a Telegram, asumiendo relación 1 a 1:
        existing_remote = RemotePayload.get_or_none(RemotePayload.payload == payload)
        if existing_remote:
            logger.info(
                f"{payload.id=} ({payload.sequence_index}) ya subido. Saltando."
            )
            return existing_remote

        if not payload.path.exists():
            raise FileNotFoundError(
                f"No se encuentra el archivo físico para {payload.path=}"
            )

        filename, caption = self._build_names(payload.path, payload)

        logger.info(f"Subiendo {payload.sequence_index=} de {payload.job.id=}...")

        with open_upload_source(
            payload.path, self.settings.upload_limit_rate_kbps
        ) as document_stream:
            progress_tracker = UploadProgress(filename)

            try:
                tg_message = self.client.send_document(
                    chat_id=self.settings.chat_id,
                    document=document_stream,
                    file_name=filename,
                    caption=caption,
                    progress=progress_tracker,
                )

                remote = RemotePayload.register_upload(payload, tg_message)

                if payload.job.strategy == Strategy.SINGLE:
                    logger.debug(f"Borrando trozo temporal: {payload.path}")
                    payload.path.unlink(missing_ok=True)

                return remote

            except Exception as e:
                logger.error(f"Error subiendo payload {payload.id}: {e}")
                raise e

    def _build_names(self, path: Path, payload: Payload) -> Tuple[str, Optional[str]]:
        """Determina el nombre del archivo y el caption para Telegram."""
        # si el nombre es muy largo, usar md5
        filename = path.name
        caption = None

        if len(filename) >= self.settings.max_filename_length:
            logger.debug(f"Nombre muy largo ({len(filename)}), usando MD5.")
            suffix = path.suffix
            filename = f"{payload.md5sum}{suffix}"
            caption = path.name  # El nombre original va al caption

        return filename, caption
