# pylint: disable=C0301
from __future__ import annotations
import os

from pyrogram import Client as ClientPyrogram
from pyrogram.types.messages_and_media.message import Message
from pyrogram.errors import (
    UserAlreadyParticipant,
    PhoneNumberInvalid,
    FloodWait,
    ChatIdInvalid,
)

from .types import MessagePlus
from .config import Config
from .utils import progress


INVITE_LINK_RE = ClientPyrogram.__dict__["INVITE_LINK_RE"]

STRING = """
> Session no encontrada.
Por favor siga los pasos de Pyrogram para autorizar su cuenta de Telegram.            
Pyrogram le pedirá su número telefonico. El número debe estar en formato internacional 
por ejemplo, para españa debe incluir el +34
"""


class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):

        if cls not in cls._instances:
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]


class Telegram(metaclass=SingletonMeta):
    def __init__(self):
        self.config = Config()

    @property
    def client(self):
        if hasattr(self, "_client") is False:
            if self.config.session_string is None:
                print(STRING)
                os.system("pause")
                print("Cargando...", end="\r")
                api_id = self.config.api_id
                api_hash = self.config.api_hash
                client = ClientPyrogram(
                    "my_account", api_id=api_id, api_hash=api_hash, in_memory=True
                )
                try:
                    client.start()
                    session_string = client.export_session_string()
                    self.config.data.update({"session_string": session_string})
                    self.config.save()
                    return client
                except PhoneNumberInvalid:
                    print("\n*Advertencia*\nEl número introducido es invalido")
                    exit()

        session_string = self.config.session_string
        client = ClientPyrogram("my_account", session_string=session_string)
        client.start()
        return client

    def update(
        self, path: str, caption: str, filename: str, progress_bar=None
    ) -> Message:
        message = self.client.send_document(
            chat_id=self.config.chat_id,
            document=path,
            file_name=filename,
            caption="" if caption == filename else caption,
            progress=progress,
            progress_args=(
                caption,
                progress_bar,
            ),
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

        if isinstance(self.config.chat_id, str):
            match = INVITE_LINK_RE.match(self.config.chat_id)

            if match:
                match = match.group()
            else:
                match = self.config.chat_id

            try:
                chatinfo = self.client.get_chat(self.config.chat_id)
                # parece get_chat devuelve el id en el formato de pyrogram
                self.config.chat_id = chatinfo.id
                self.config.data.update({"chat_id": self.config.chat_id})
                self.config.save()
            except ChatIdInvalid:
                print(
                    "No se pudo obtener la info del chat_id. Asegurate que el chat_id este en el formato de pyrogram o que sea un enlace de invitación de Telegram"
                )
                exit()

        else:
            chatinfo = self.client.get_chat(self.config.chat_id)

        if getattr(chatinfo, "id", False) is False:
            print(f"El usuario no hace parte de chat_id {self.config.chat_id}")
            exit()
        if not chatinfo.permissions.can_send_media_messages:
            print(
                f"No tienes permisos para subir archivos en chat_id {self.config.chat_id}"
            )
            exit()
        print("CHAT_ID:", chatinfo.title)

    def check_session(self):
        """
        Comprueba si el usuario está logeado en Telegram.
        """
        user = self.client.get_users("me")
        print(f"{user.username or user.first_name}", "¡está logeado!\n")
