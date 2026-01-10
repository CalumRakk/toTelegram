import locale
import logging
from contextlib import contextmanager

from totelegram.core.setting import Settings

_client_instance = None


def parse_message_json_data(json_data: dict):
    from pyrogram.enums import MessageMediaType
    from pyrogram.types import Chat, Message

    data = json_data.copy()
    data.pop("_")

    chat_json = data["chat"].copy()
    chat_json.pop("_")
    chat = Chat(**chat_json)

    media_type_str = data["media"].split(".")[-1].lower()
    media_type = MessageMediaType(media_type_str)

    data["chat"] = chat
    data["media"] = media_type

    message = Message(**data)
    return message


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
        settings.profile_name,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        workdir=str(settings.profile_path),
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


def stop_telegram_client():
    global _client_instance
    if _client_instance:
        _client_instance.stop()  # type: ignore
        _client_instance = None
