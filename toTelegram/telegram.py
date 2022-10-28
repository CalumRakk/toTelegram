

from __future__ import annotations
import os.path
from typing import Union, Optional

from pyrogram import Client  # pip install pyrogram
from pyrogram.types.messages_and_media.message import Message

from .config import Config
from .functions import (get_part_filepart, progress,
                        attributes_to_json, progress)
from pyrogram.types.messages_and_media.document import Document
from pyrogram.types.messages_and_media.message import Message


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
        if json_data==None:
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


class Telegram:
    def __init__(self, config: Config):
        self.config = config
        self.is_client_initialized = False

    def _start(self):
        client = Client(self.USERNAME, self.API_ID,
                        self.API_HASH, workdir="setting")
        client.start()
        return client

    @property
    def CHAT_ID(self):
        return self.config.chat_id

    @property
    def USERNAME(self):
        return self.config.username

    @property
    def API_ID(self):
        return self.config.api_id

    @property
    def API_HASH(self):
        return self.config.api_hash

    @property
    def get_client(self):
        return Client(self.USERNAME, self.API_ID, self.API_HASH)

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
            chat_id=self.CHAT_ID,
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


config = Config("setting\config.yaml")
telegram = Telegram(config)
