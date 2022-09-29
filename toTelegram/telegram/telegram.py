from __future__ import annotations

from pyrogram import Client  # pip install pyrogram
from pyrogram.types.messages_and_media.message import Message

from .messageplus import Messageplus
from .config import Config
from ..functions import progress

class Telegram:
    def __init__(self, config: Config):
        self.config= config
        self.is_client_initialized = False

    def _start(self):
        client = Client(self.USERNAME, self.API_ID, self.API_HASH,workdir= "setting")
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
        # ValueError
        # pyrogram.errors.exceptions.bad_request_400.UsernameInvalid
        # caption= filepart if temp.exceed_file_size_limit else ""
        if caption==filename:
            caption=""
        message = self.client.send_document(
            chat_id=self.CHAT_ID,
            document=path,
            file_name=filename,
            caption=caption,
            progress=progress)
        return Messageplus(message)

    def get_message(self, link: str) -> Messageplus:
        chat_id = "-100" + link.split("/")[-2]
        iD = int(link.split("/")[-1])
        message = self.client.get_messages(chat_id, iD)
        return Messageplus(message)

config= Config("setting\config.yaml")
telegram= Telegram(config)