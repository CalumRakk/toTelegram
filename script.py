import logging
from pathlib import Path
from totelegram.logging_config import setup_logging
from totelegram.setting import get_settings
from totelegram.uploader.handlers import main

if __name__ == "__main__":
    setup_logging(f"{__file__}.log", logging.DEBUG)
    settings = get_settings("env/test.env")
    target = Path(
        r"D:\\github Leo\\toTelegram\\tests\\Otan Mian Anoixi (Live - Bonus Track)-(240p).mp4"
    )
    main(target=target, settings=settings)
