# pylint: disable=C0301
from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import cast

from pyrogram import Client as ClientPyrogram
from pyrogram.errors import (
    ChatIdInvalid,
    FloodWait,
    PhoneNumberInvalid,
    UserAlreadyParticipant,
)
from pyrogram.errors.exceptions.bad_request_400 import UserAlreadyParticipant
from pyrogram.types.messages_and_media.message import Message
from pyrogram.types.user_and_chats.chat import Chat

from .config import Config
from .types import MessagePlus
from .utils import progress

INVITE_LINK_RE = ClientPyrogram.__dict__["INVITE_LINK_RE"]

STRING = """
> Session no encontrada.
Por favor siga los pasos de Pyrogram para autorizar su cuenta de Telegram.            
Pyrogram le pedirá su número telefonico. El número debe estar en formato internacional 
por ejemplo, para españa debe incluir el +34
"""


class Telegram:
    def __init__(self, config: Config):
        self.config = config

    @property
    def client(self) -> ClientPyrogram:
        if hasattr(self, "_client") is False:
            name = self.config.name
            api_id = self.config.api_id
            api_hash = self.config.api_hash
            client = ClientPyrogram(
                name,
                api_id=api_id,
                api_hash=api_hash,
                workdir=str(self.config.worktable),
            )
            client.start()
            setattr(self, "_client", client)
            return client
        return getattr(self, "_client")

    def update(self, path: str, caption: str, filename: str) -> Message:
        log_capture_string = io.StringIO()
        # Crear un manejador que escriba en el StringIO
        logging.basicConfig(
            level=logging.WARNING,  # Establecer el nivel de logging
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler(log_capture_string)],
        )

        message = self.client.send_document(
            chat_id=self.config.chat_id,
            document=path,
            file_name=filename,
            caption="" if caption == filename else caption,
            progress=progress,
            progress_args=(caption, log_capture_string),
        )
        return MessagePlus.from_message(message)

    def download(self, message_plus: MessagePlus, path=None) -> str:
        """descarga un archivo de Telegram

        Args:
            message_plus: objeto de la clase MessagePlus
            path: Una ruta personalizada para guardar el archivo. Si no está presente el archivo se descarga en la carpeta de trabajo.

        Returns:
            str: ruta completa de donde se descargo el archivo.
        """
        chat_id = message_plus.chat_id
        message_id = message_plus.message_id

        output = path or os.path.join(self.config.worktable, message_plus.file_name)
        if not os.path.exists(output):
            message = self.client.get_messages(chat_id, message_id)
            self.client.download_media(
                message,
                file_name=output,
                progress=progress,
                progress_args=(message_plus.file_name,),
            )
        return output

    def get_message(self, messageplus: MessagePlus) -> MessagePlus:

        return self.client.get_messages(
            int(str(self.config.chat_id).replace("-", "-100")), messageplus.message_id
        )

    def join_group(self, invite_link):
        """
        Intenta entrar a un grupo
        """
        try:
            return self.client.join_chat(invite_link)
        except UserAlreadyParticipant:
            return self.client.get_chat(self.config.chat_id)
        except FloodWait as e:
            print("Pyrogram ha generado una espera.", e.MESSAGE)
            exit()

    def check_chat_id(self):
        """
        Prueba si hace parte del grupo y prueba si tiene permisos para subir archivos.
        - Entra al grupo si chat_id es una invitación valida
        """

        def is_chat_id_invite_link(chat_id):
            chat_id = str(chat_id) if isinstance(chat_id, int) else chat_id
            match = INVITE_LINK_RE.match(chat_id)
            return match is not None

        def join_and_get_chat(invite_link) -> Chat:
            if not is_chat_id_invite_link(chat_id):
                raise ChatIdInvalid("invite_link no es valido")
            try:
                invite_link = INVITE_LINK_RE.match(chat_id).group()
                chatinfo = self.client.join_chat(invite_link)
                return chatinfo
            except UserAlreadyParticipant:
                return self.client.get_chat(self.config.chat_id)

        chat_id = self.config.chat_id
        if is_chat_id_invite_link(chat_id):
            chatinfo = join_and_get_chat(chat_id)
            message = cast(Message, self.client.send_message(chatinfo.id, STRING))
            message.delete()
            self.client.loop.run_until_complete(self.client.storage.save())
            chat_id = getattr(chatinfo, "id")
            self.config.chat_id = chat_id
            self.config.data.update({"chat_id": self.config.chat_id})
            self.config.save()
        else:
            chatinfo = self.client.get_chat(self.config.chat_id)

    def check_session(self):
        """
        Comprueba si el usuario está logeado en Telegram.
        """
        user = self.client.get_users("me")
        print(f"{user.username or user.first_name}", "¡está logeado!\n")
