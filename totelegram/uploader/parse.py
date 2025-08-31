from pyrogram.types.messages_and_media.message import Message
from pyrogram.enums import MessageMediaType
from pyrogram.types.user_and_chats.chat import Chat

def parse_message_json_data(json_data: dict):
    data = json_data.copy()
    data.pop("_")

    chat_json = data["chat"].copy()
    chat_json.pop("_")
    chat = Chat(**chat_json)

    # Solo está disponible para miembros del grupo. Si eres propietario no aparece.
    # user_json = data["from_user"].copy()
    # user_json.pop("_")
    # user = types.User(**user_json)

    media_type_str= data["media"].split(".")[-1].lower()
    media_type= MessageMediaType(media_type_str)

    data["chat"] = chat
    # data["from_user"] = user
    data["media"] = media_type

    message = Message(**data)
    return message