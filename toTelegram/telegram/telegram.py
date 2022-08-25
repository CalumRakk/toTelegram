from __future__ import annotations
import os.path

from pyrogram import Client  # pip install pyrogram
from pyrogram.types.messages_and_media.message import Message

from .messageplus import Messageplus
from ..config import Config
from ..functions import progress


class Telegram(Config):
    def __init__(self):
        super().__init__()
        self.is_client_initialized = False

    def _start(self):
        client = Client(self.USERNAME, self.API_ID, self.API_HASH)
        client.start()
        return client

    @property
    def get_client(self):
        return Client(self.USERNAME, self.API_ID, self.API_HASH)

    @property
    def client(self) -> Client:
        if not self.is_client_initialized:
            self._client = self._start()
            self.is_client_initialized = True
        return self._client

    def update(self, path: str, caption=None) -> Message:
        # ValueError
        # pyrogram.errors.exceptions.bad_request_400.UsernameInvalid
        # caption= filepart if temp.exceed_file_size_limit else ""
        filename = os.path.basename(path)

        message = self.client.send_document(
            chat_id=self.CHAT_ID,
            document=path,
            file_name=filename,
            caption=filename if caption else "",
            progress=progress)
        return Messageplus(message)

    def get_message(self, link: str) -> Messageplus:
        chat_id = "-100" + link.split("/")[-2]
        iD = int(link.split("/")[-1])
        message = self.client.get_messages(chat_id, iD)
        return Messageplus(message)

telegram= Telegram()