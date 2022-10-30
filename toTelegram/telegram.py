

from __future__ import annotations
import os.path
from typing import Union, Optional

from pyrogram import Client  # pip install pyrogram
from pyrogram.types.messages_and_media.message import Message
from pyrogram.types import User

# from .config import Config
from .functions import (get_part_filepart, progress,
                        attributes_to_json, progress)
from pyrogram.types.messages_and_media.document import Document
from pyrogram.types.messages_and_media.message import Message
from pyrogram.errors import UserAlreadyParticipant


INVITE_LINK_RE= Client.__dict__["INVITE_LINK_RE"]

telegram=None

class MessagePlus:
    @classmethod
    def from_message(cls, message):
        """
        message: Objeto de la clase Message de pyrogram
        """
        if message:
            mediatype = message.media
            if type(mediatype) == str:
                media: Document = getattr(message, mediatype)
            else:
                media: Document = getattr(message, mediatype.value)

        file_name = media.file_name
        message_id = message.message_id if getattr(
            message, "message_id", None) else message.id
        size = media.file_size
        chat_id = message.chat.id
        link = message.link
        return MessagePlus(file_name=file_name, message_id=message_id, size=size, chat_id=chat_id, link=link)

    @classmethod
    def from_json(cls, json_data):
        if json_data == None:
            return None
        file_name = json_data["file_name"]
        message_id = json_data["message_id"]
        size = json_data["size"]
        chat_id = json_data["chat_id"]
        link = json_data["link"]
        return MessagePlus(file_name=file_name, message_id=message_id, size=size, chat_id=chat_id, link=link)

    def __init__(self,
                 file_name: Optional[int] = None,
                 message_id: Optional[int] = None,
                 size: Optional[int] = None,
                 chat_id: Optional[int] = None,
                 link: Optional[int] = None,
                 ) -> None:
        self.file_name = file_name
        self.message_id = message_id
        self.size = size
        self.chat_id = chat_id
        self.link = link

    def to_json(self) -> dict:
        return attributes_to_json(self)

    # def download(self) -> str:
    #     path = os.path.join(WORKTABLE, self.file_name)
    #     if os.path.exists(path) and os.path.getsize(path) == self.size:
    #         return path
    #     return self.message.download(file_name=path, progress=progress)

    @property
    def message(self) -> Message:
        if getattr(self, "_message", None) is None:
            self._message = telegram.client.get_messages(
                self.chat_id, self.message_id)
        return self._message

class Api:
    def __init__(self, api_hash, api_id):
        self.api_hash = api_hash
        self.api_id = api_id

class Telegram:
    def __init__(self, api_hash, api_id, chat_id: int=None, name: str = "me"):
        self.name = name
        self.api = Api(api_hash=api_hash,api_id=api_id )
        self.chat_id = chat_id
        
        self.is_client_initialized = False

    
    @property
    def api_hash(self):
        return self.api.api_hash

    @property
    def api_id(self):
        return self.api.api_id

    def _start(self):
        client = Client(name=self.name, api_id=self.api_id, api_hash=self.api_hash,workdir="setting")
        client.start()
        return client

    @property
    def client(self) -> Client:
        if not self.is_client_initialized:
            self._client = self._start()
            self.is_client_initialized = True
        return self._client

    def update(self, path: str, caption: str, filename: str) -> Message:
        if caption == filename:
            caption = ""
        message = self.client.send_document(
            chat_id=self.chat_id,
            document=path,
            file_name=filename,
            caption=caption,
            progress=progress)
        return MessagePlus.from_message(message)

    def get_message(self, link: str) -> MessagePlus:
        chat_id = "-100" + link.split("/")[-2]
        iD = int(link.split("/")[-1])
        message = self.client.get_messages(chat_id, iD)
        return MessagePlus.from_message(message)

    # def get_user_information(self):
    #     return self.client.get_users("me")
    
    
    def test_session(self):
        user= self.client.get_users("me")
        if type(user)==User:
            print("El username del usuario logeado es", user.username)
        print("El usuario no está logeado.")
    
    def test_group(self, chat_id):
        pass
        
    
    def _join_group(self,invite_link):
        """
        Intenta entrar a un grupo
        """
        try:
            self.client.join_chat(invite_link)
        except UserAlreadyParticipant:
            return   
    def get_id_from_chat(self, chat_id: Union[int, str]):
        """
        Parametro:
            chat_id (``int`` | ``str``):
                Identificador único (int) o nombre de usuario (str) del chat de destino.
                Identificador único para el chat de destino en forma de enlace *t.me/joinchat/*, identificador (int) 
                o username del canal/supergrupo de destino (en el formato @username).
        """        
        match = INVITE_LINK_RE.match(chat_id)
        if match:
            self._join_group(chat_id)            
        chat= self.client.get_chat(chat_id)
        return chat.id
        
            
            
        

# config = Config("config.yaml")
# telegram = Telegram(config)
