import logging
from math import log
from pathlib import Path
from typing import List
import unittest
import sys
import os
sys.path.append(os.getcwd())

from totelegram.logging_config import setup_logging
from totelegram.uploader.database import init_database
from unittest.mock import MagicMock, patch
from totelegram.models import File, FileCategory, FileStatus, Message, Piece, db_proxy
from totelegram.setting import get_settings
from totelegram.uploader.handlers import upload, upload_file
from totelegram.uploader.telegram import init_telegram_client
from pyrogram import types


class TestSendFile(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        logging.shutdown()
    def setUp(self):  
        self.settings = get_settings("env/test.env")        
        self.settings.database_name = "test.db"
        self.target = Path(r"tests\medias\Otan Mian Anoixi (Live - Bonus Track)-(240p).mp4")
    def _remove_messages(self, messages: List[types.Message]):
        logger= logging.getLogger(__name__)
        logger.info(f"Borrando {len(messages)} archivos subidos en Telegram")

        to_remove= {}
        for message in messages:
            chat_id= message.chat.id
            if chat_id in to_remove.keys():
                to_remove[chat_id].append(message.id)
            else:
                to_remove[chat_id]= [message.id]
        
        client= init_telegram_client(self.settings) 
        for chat_id, messages_ in to_remove.items():
            logger.info(f"Borrando los archivos subidos al chat {chat_id}")
            client.delete_messages(chat_id, messages_)  # type: ignore
            logging.info(f"Se borraron {len(messages_)} subidos al chat {chat_id}")  
    def test_upload_single_file(self): 
        try: 
            setup_logging(r"tests\logs\test_upload_single_file.log", logging.DEBUG)   
            init_database(self.settings)
            result = upload(target=self.target, settings=self.settings)
            file: File = result[0]

            with self.subTest("resultado tiene un elemento"):
                self.assertEqual(len(result), 1)

            with self.subTest("archivo es de tipo single-file"):
                self.assertEqual(file.type, FileCategory.SINGLE)

            with self.subTest("archivo está subido"):
                self.assertEqual(file.status, FileStatus.UPLOADED)

            message= file.message.get_message()
            self._remove_messages([message])

        finally:
            logger= logging.getLogger(__name__)
            logger.info("Borrando base de datos")
            db_proxy.close()
            self.settings.database_path.unlink()
            logger.info("Base de datos borrada")


    def test_upload_pieces_file(self):
        try: 
            setup_logging(r"tests\logs\test_upload_pieces_file.log", logging.DEBUG) 
            init_database(self.settings)     
            file_size= self.target.stat().st_size
            self.settings.max_filesize_bytes= int(file_size / 2)

            result = upload(target=self.target, settings=self.settings)
            file: File = result[0]

            with self.subTest("resultado tiene un elemento"):
                self.assertEqual(len(result), 1)

            with self.subTest("archivo es de tipo single-file"):
                self.assertEqual(file.type, FileCategory.CHUNKED)

            with self.subTest("archivo está subido"):
                self.assertEqual(file.status, FileStatus.UPLOADED)

            messages= [piece.message.get_message() for piece in file.pieces]
            self._remove_messages(messages)

        finally:
            logger= logging.getLogger(__name__)
            logger.info("Borrando base de datos")
            db_proxy.close()
            self.settings.database_path.unlink()
            logger.info("Base de datos borrada")
    

      
      

if __name__ == "__upload__":
    unittest.main()