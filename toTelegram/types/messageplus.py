

from pyrogram.types.messages_and_media.document import Document
from ..utils import attributes_to_json

class MessagePlus:
    def __init__(self,
                 file_name: str,
                 message_id: int,
                 size: int,
                 chat_id: int,
                 link: str,
                 ) -> None:
        self.file_name = file_name
        self.message_id = message_id
        self.size = size
        self.chat_id = chat_id
        self.link = link
    def to_json(self) -> dict:
        return attributes_to_json(self)
    @classmethod
    def from_message(cls, message):
        """
        message: Objeto de la clase Message de pyrogram
        """
        if message:
            mediatype = message.media
            if isinstance(mediatype, str):
                media: Document = getattr(message, mediatype)
            else:
                media: Document = getattr(message, mediatype.value)

        file_name = media.file_name
        message_id = message.message_id if getattr(
            message, "message_id", None) else message.id
        size = media.file_size
        chat_id = message.chat.id
        link = message.link
        return MessagePlus(file_name=file_name,
                           message_id=message_id,
                           size=size,
                           chat_id=chat_id,
                           link=link)

    @classmethod
    def from_json(cls, json_data):
        if json_data is None:
            return None
        file_name = json_data["file_name"]
        message_id = json_data["message_id"]
        size = json_data["size"]
        chat_id = json_data["chat_id"]
        link = json_data["link"]
        return MessagePlus(file_name=file_name,
                           message_id=message_id,
                           size=size,
                           chat_id=chat_id,
                           link=link)
