def parse_message_json_data(json_data: dict):
    from pyrogram.enums import MessageMediaType
    from pyrogram.types import Chat, Message

    data = json_data.copy()
    data.pop("_")

    chat_json = data["chat"].copy()
    chat_json.pop("_")
    chat = Chat(**chat_json)

    media_type_str = data["media"].split(".")[-1].lower()
    media_type = MessageMediaType(media_type_str)

    data["chat"] = chat
    data["media"] = media_type

    message = Message(**data)
    return message
