import logging
from pathlib import Path
import unittest
import sys
import os
sys.path.append(os.getcwd())

from totelegram.logging_config import setup_logging
from totelegram.uploader.database import init_database
from unittest.mock import MagicMock, patch
from totelegram.models import File, FileCategory, FileStatus, Message, db_proxy
from totelegram.setting import get_settings
from totelegram.uploader.handlers import main, upload_file
from totelegram.uploader.telegram import init_telegram_client
from pyrogram import types


class TestSendFile(unittest.TestCase):
    def test_upload_single_file(self):
        setup_logging(f"{__file__}.log", logging.DEBUG)
        target = Path(r"tests\medias\Otan Mian Anoixi (Live - Bonus Track)-(240p).mp4")
        settings = get_settings("env/test.env")
        settings.database_name = "test.db"  
        try:     
            result = main(target=target, settings=settings)
            file: File = result[0]

            with self.subTest("resultado tiene un elemento"):
                self.assertEqual(len(result), 1)

            with self.subTest("archivo es de tipo single-file"):
                self.assertEqual(file.type, FileCategory.SINGLE)

            with self.subTest("archivo está subido"):
                self.assertEqual(file.status, FileStatus.UPLOADED)

            message= file.message.get_message()
            logging.info(f"Borrando el archivo subido en Telegram: {message.link}")
            client= init_telegram_client(settings) 
            client.delete_messages(message.chat.id, message.id)  # type: ignore
            logging.info("Archivo borrado satisfactoriamente")  
        
        finally:
            logging.info("Borrando base de datos")
            db_proxy.close()
            settings.database_path.unlink()
            logging.info("Base de datos borrada")
 
      
      

if __name__ == "__main__":
    unittest.main()