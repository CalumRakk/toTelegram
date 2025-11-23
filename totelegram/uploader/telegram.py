import locale
import logging
from contextlib import contextmanager

from totelegram.models import File, FileCategory
from totelegram.setting import Settings

_client_instance = None


def init_telegram_client(settings: Settings):
    global _client_instance

    if _client_instance:
        return _client_instance

    logger = logging.getLogger(__name__)
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
    _client_instance = client
    return client


@contextmanager
def telegram_client_context(settings: Settings):
    """
    Context manager para manejar el ciclo de vida del cliente de Telegram.
    Garantiza que stop() se llame siempre, incluso si hay errores.
    """
    client = init_telegram_client(settings)
    try:
        yield client
    finally:
        stop_telegram_client()


def is_empty_message(client, file: File):
    """Comprobar si el File sigue disponible en Telegram.

    Nota: Si es CHUNKED, devuelve True si alguna de las piezas no se encuentra en Telegram.
    """
    from pyrogram.types import Message

    if file.get_category() == FileCategory.SINGLE:
        message_db = file.message_db
        message: Message = client.get_messages(message_db.chat_id, message_db.message_id)  # type: ignore
        if message.empty:
            return True
        return False
    elif file.get_category() == FileCategory.CHUNKED:
        for piece in file.pieces:
            chat_id = piece.message_db.chat_id
            message_id = piece.message_db.message_id
            message: Message = client.get_messages(chat_id, message_id)  # type: ignore
            if message.empty:
                return True
        return False


def stop_telegram_client():
    global _client_instance
    if _client_instance:
        _client_instance.stop()  # type: ignore
        _client_instance = None
