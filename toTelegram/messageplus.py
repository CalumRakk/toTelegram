from pyrogram.types.messages_and_media.document import Document
from pyrogram.types.messages_and_media.message import Message

class Messageplus:
    def __init__(self, message : Message) -> None:
        mediatype = message.media
        if type(mediatype) == str:
            media: Document = getattr(message, mediatype)
        else:
            media: Document = getattr(message, mediatype.value)

        self.filename = media.file_name
        self.message_id = message.message_id if getattr(message, "message_id", None) else message.id
        self.link = message.link
        self.size = media.file_size
        self.chat_id = message.chat.id
