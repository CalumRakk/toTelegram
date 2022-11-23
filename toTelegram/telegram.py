
from __future__ import annotations
import os

from pyrogram import Client as ClientPyrogram
from pyrogram.types.messages_and_media.message import Message
from pyrogram.errors import UserAlreadyParticipant, PhoneNumberInvalid, FloodWait, ChatIdInvalid

from .types import MessagePlus
from .config import Config
from .functions import progress


INVITE_LINK_RE = ClientPyrogram.__dict__["INVITE_LINK_RE"]

STRING = """
> Session no encontrada.
Por favor siga los pasos de Pyrogram para autorizar su cuenta de Telegram.            
Pyrogram le pedirá su número telefonico. El número debe estar en formato internacional 
por ejemplo, para españa debe incluir el +34
"""


class Client:
    def __init__(self):
        self.api_hash = Config.api_hash
        self.api_id = Config.api_id
        self.chat_id = Config.chat_id
        self.session_string = Config.session_string

    def __get__(self, obj, other):
        if getattr(self, "_client", False) is False:
            setattr(self,"_client", self._get_client())
        return getattr(self, "_client")

    def _get_client(self):
        if getattr(self, "session_string", False) is False:
            print(STRING)
            os.system("pause")
            print("Cargando...", end="\r")

            client = ClientPyrogram("my_account", api_id=self.api_id,
                                    api_hash=self.api_hash, in_memory=True)
            try:
                client.start()
                self.session_string = client.export_session_string()
                Config.insert_or_update_field(
                    {"session_string": self.session_string})
                return client
            except PhoneNumberInvalid:
                print("\n*Advertencia*\nEl número introducido es invalido")
                exit()

        client = ClientPyrogram(
            "my_account", session_string=self.session_string)
        client.start()
        return client


class Telegram:
    api_hash = Config.api_hash
    api_id = Config.api_id
    chat_id = Config.chat_id
    session_string = Config.session_string
    chat_id = Config.chat_id
    client: ClientPyrogram = Client()

    @classmethod
    def update(cls, path: str, caption: str, filename: str) -> Message:
        message = cls.client.send_document(
            chat_id=cls.chat_id,
            document=path,
            file_name=filename,
            caption="" if caption == filename else caption,
            progress=progress,
            progress_args=(caption,)
        )
        return MessagePlus.from_message(message)

    # def get_message(self, link: str) -> MessagePlus:
    #     chat_id = "-100" + link.split("/")[-2]
    #     iD = int(link.split("/")[-1])
    #     message = self.client.get_messages(chat_id, iD)
    #     return MessagePlus.from_message(message)

    @classmethod
    def join_group(cls, invite_link):
        """
        Intenta entrar a un grupo
        """
        try:
            return cls.client.join_chat(invite_link)
        except UserAlreadyParticipant:
            return cls.client.get_chat(cls.chat_id)
        except FloodWait as e:
            print("Pyrogram ha generado una espera.", e.MESSAGE)
            exit()

    @classmethod
    def check_chat_id(cls):
        """
        Prueba si hace parte del grupo y prueba si tiene permisos para subir archivos.
        - Entra al grupo si chat_id es una invitación valida
        """
        if isinstance(cls.chat_id, str):
            match = INVITE_LINK_RE.match(cls.chat_id)
            if match:
                chatMember = cls.join_group(cls.chat_id)
                cls.chat_id = chatMember.id
                Config.insert_or_update_field({"chat_id": cls.chat_id})

            # Podría ser un el valor @username
            chatMember = cls.client.get_chat(cls.chat_id)
        else:
            try:
                chatMember = cls.client.get_chat(cls.chat_id)
            except ChatIdInvalid:
                chat_id = int("-100" + str(cls.chat_id).replace("-", ""))
                chatMember = cls.client.get_chat(chat_id)
                cls.chat_id = chat_id

        if getattr(chatMember, "id", False) is False:
            print(f"El usuario no hace parte de chat_id {cls.chat_id}")
            exit()
        if not chatMember.permissions.can_send_media_messages:
            print(
                f"No tienes permisos para subir archivos en chat_id {cls.chat_id}")
            exit()
        print("CHAT_ID:", chatMember.title)

    @classmethod
    def check_session(cls):
        """
        Comprueba si el usuario está logeado en Telegram.
        """
        user = cls.client.get_users("me")
        print(f"{user.username or user.first_name}", "¡está logeado!\n")
