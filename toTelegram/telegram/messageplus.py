import os.path
from typing import Union, Optional

from pyrogram.types.messages_and_media.document import Document
from pyrogram.types.messages_and_media.message import Message

from ..functions import get_part_filepart, progress
from ..constants import WORKTABLE


class Messageplus:
    def __init__(self,
                 message: Optional[Message]=None, # Si message está presente no se requiere rellenar los demás atributos
                 file_name: Optional[int] = None,
                 message_id: Optional[int] = None,
                 size: Optional[int] = None,
                 chat_id: Optional[int] = None,
                 link: Optional[int] = None,
                 ) -> None:
        if message:
            mediatype = message.media
            if type(mediatype) == str:
                media: Document = getattr(message, mediatype)
            else:
                media: Document = getattr(message, mediatype.value)

        self.file_name = file_name or media.file_name
        self.message_id = message_id if message_id else message.message_id if getattr(
            message, "message_id", None) else message.id
        self.size = size or media.file_size
        self.chat_id = chat_id or message.chat.id
        self.link = link or message.link

    def to_json(self) -> dict:
        return self.__dict__.copy()

    def download(self) -> str:
        path = os.path.join(WORKTABLE, self.file_name)
        if os.path.exists(path) and os.path.getsize(path) == self.size:
            return path
        return self.message.download(file_name=path, progress=progress)

    @property
    def telegram(self):
        if getattr(self, "_telegram", None) is None:
            from .telegram import TELEGRAM
            self._telegram = TELEGRAM
        return self._telegram

    @property
    def message(self) -> Message:
        if getattr(self, "_message", None) is None:
            self._message = self.telegram.client.get_messages(
                self.chat_id, self.message_id)
        return self._message

    @property
    def part(self):
        return get_part_filepart(self.file_name)

    @property
    def filename(self):
        return self.file_name.replace("_"+self.part, "")
