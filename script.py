import logging
from pathlib import Path
from totelegram.logging_config import setup_logging
from totelegram.setting import get_settings
from totelegram.uploader.handlers import upload

if __name__ == "__main__":
    setup_logging(f"{__file__}.log", logging.DEBUG)
    settings = get_settings("env/.env")
    target = Path(
        r"C:\Users\Leo\Videos\Replay 2025-08-23 00-39-04.mkv"
    )
    upload(target=target, settings=settings)
