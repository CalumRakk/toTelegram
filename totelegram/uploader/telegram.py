# uploader/telegram_service.py
import locale
import logging
from totelegram.models import File, FileCategory
from totelegram.setting import Settings
from pyrogram.types.messages_and_media.message import Message as MessageTg
_client_instance = None

def init_telegram_client(settings: Settings):
    global _client_instance

    if _client_instance:
        return _client_instance

    logger= logging.getLogger(__name__)
    logger.info("Iniciando cliente de Telegram")

    from pyrogram.client import Client

    lang, encoding = locale.getdefaultlocale()
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
    _client_instance= client
    return client


def is_empty_message(client, file:File):
    """Comprobar si el File sigue disponible en Telegram.
    
    Nota: Si es CHUNKED, devuelve True si alguna de las piezas no se encuentra en Telegram.
    """
    if file.get_category() == FileCategory.SINGLE:
        message_tg: MessageTg= client.get_messages(message.chat_id, message.message_id) # type: ignore
        if message_tg.empty:
            return True
        return False
    elif file.get_category() == FileCategory.CHUNKED:
        for piece in file.pieces:
            chat_id= piece.message.chat_id
            message_id= piece.message.message_id
            message_tg: MessageTg= client.get_messages(chat_id, message_id) # type: ignore
            if message_tg.empty:
                return True
        return False
    