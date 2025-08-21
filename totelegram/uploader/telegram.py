# uploader/telegram_service.py
import locale
import logging
from pathlib import Path
from typing import Optional

from totelegram.models import File, Piece
from totelegram.setting import Settings

logger= logging.getLogger(__name__)

def init_telegram_client(settings: Settings):
    logger.info("Iniciando cliente de Telegram")

    from pyrogram.client import Client

    lang, encoding = locale.getlocale()
    iso639 = "en"
    if lang:
        iso639 = lang.split("_")[0]

    client = Client(
        settings.session_name,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        workdir=str(settings.worktable),
        lang_code=iso639,
    )
    client.start()  # type: ignore
    logger.info("Cliente de Telegram inicializado correctamente")
    return client


class TelegramService:
    def __init__(self, client, settings):
        self.client = client
        self.settings = settings

    def send_file(
        self,
        file_path: Path,
        filename: Optional[str] = None,
        caption: Optional[str] = None,
    ):
        send_data = {"chat_id": self.settings.chat_id, "document": str(file_path)}
        if filename:
            send_data["file_name"] = filename
        if caption:
            send_data["caption"] = caption
        return self.client.send_document(**send_data)
