import logging
from pathlib import Path
from totelegram.logging_config import setup_logging
from totelegram.setting import get_settings
from totelegram.uploader.handlers import upload

if __name__ == "__main__":
    setup_logging(f"{__file__}.log", logging.DEBUG)
    settings = get_settings("env/public.env")
    target = Path(
        r"C:\Users\Leo\Downloads\tsetup-x64.6.0.2.exe"
    )
    upload(target=target, settings=settings)
