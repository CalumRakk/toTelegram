from __future__ import annotations

import json
import locale
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from totelegram.core.setting import Settings

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.enums import MessageMediaType
    from pyrogram.types import Chat, Message

logger = logging.getLogger(__name__)


class TelegramSession:
    """
    Context Manager centralizado para manejar el ciclo de vida del cliente de Telegram.
    Soporta inicialización vía objeto Settings (uso normal) o parámetros directos (validación).
    """

    def __init__(
        self,
        worktable: Path | str,
        settings: Optional[Settings] = None,
        session_name: Optional[str] = None,
        api_id: Optional[int] = None,
        api_hash: Optional[str] = None,
    ):
        self.client: Optional[Client] = None

        if settings:
            self.name = settings.profile_name
            self.api_id = settings.api_id
            self.api_hash = settings.api_hash
            self.workdir = worktable
        else:
            if not all([session_name, api_id, api_hash, worktable]):
                raise ValueError(
                    "Debes proveer 'settings' O 'session_name, api_id, api_hash, workdir'"
                )

            self.name = session_name
            self.api_id = api_id
            self.api_hash = api_hash
            self.workdir = worktable

    def start(self) -> Client:
        """Inicia la conexión manualmente."""
        if self.client and self.client.is_connected:
            return self.client

        logger.info(f"Iniciando sesión de Telegram: {self.name}")
        from pyrogram import Client  # type: ignore

        lang, encoding = locale.getdefaultlocale()
        iso639 = lang.split("_")[0] if lang else "en"

        self.client = Client(
            name=self.name,  # type: ignore
            api_id=self.api_id,  # type: ignore
            api_hash=self.api_hash,  # type: ignore
            workdir=str(self.workdir),  # type: ignore
            lang_code=iso639,
            in_memory=False,
            no_updates=True,
        )

        try:
            self.client.start()  # type: ignore
            logger.debug("Cliente Pyrogram iniciado correctamente")
            return self.client
        except Exception as e:
            logger.error(f"Error iniciando cliente Telegram: {e}")
            raise e

    def stop(self):
        """Detiene la conexión manualmente."""
        if self.client and self.client.is_connected:
            logger.info("Cerrando sesión de Telegram...")
            try:
                self.client.stop()  # type: ignore
            except Exception as e:
                logger.warning(f"Error al cerrar cliente (ignorable): {e}")
        self.client = None

    def __enter__(self) -> Client:
        """Soporte para Context Manager (with)."""
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Soporte para Context Manager (with)."""
        self.stop()


def parse_message_json_data(json_data: dict) -> Message:
    """Utilidad para reconstruir objetos Message desde JSON almacenado en BD."""
    from pyrogram.enums import MessageMediaType
    from pyrogram.types import Chat, Message

    if isinstance(json_data, str):
        json_data = json.loads(json_data)

    data = json_data.copy()

    # Traducimos message_id (nombre en JSON) a id (nombre en constructor)
    if "message_id" in data:
        data["id"] = data.pop("message_id")

    if "_" in data:
        data.pop("_")

    chat_json = data["chat"].copy()

    if "_" in chat_json:
        chat_json.pop("_")
    chat = Chat(**chat_json)

    media_raw = data.get("media")
    media_type = None
    if isinstance(media_raw, str) and "." in media_raw:

        media_type_str = media_raw.split(".")[-1].lower()
        media_type = MessageMediaType(media_type_str)
    elif media_raw:
        media_type = media_raw

    data["chat"] = chat
    data["media"] = media_type

    data.pop("link", "")
    return Message(**data)
