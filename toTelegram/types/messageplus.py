
from typing import Optional
from pyrogram.types.messages_and_media.document import Document
from ..functions import attributes_to_json
from pyrogram.types.messages_and_media.message import Message

class MessagePlus:
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

    # @property
    # # TODO: Esta propiedad hace uso de la clase Telegram, pero si se importa ocurre un error de importacion circular.
    # def message(self) -> Message:
    #     if getattr(self, "_message", None) is None:
    #         self._message = self.telegram.client.get_messages(
    #             self.chat_id, self.message_id)
    #     return self._message
    
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
