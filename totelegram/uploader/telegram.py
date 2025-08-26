# uploader/telegram_service.py
import locale
import logging
from totelegram.setting import Settings

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
