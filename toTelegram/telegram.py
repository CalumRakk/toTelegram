
from __future__ import annotations
from tempfile import gettempdir
from typing import Union, Optional
import yaml
import os
import time

from pyrogram import Client  # pip install pyrogram
from pyrogram.types.messages_and_media.message import Message
from pyrogram.types import User

from .functions import (progress,
                        attributes_to_json, progress)
from pyrogram.types.messages_and_media.document import Document
from pyrogram.types.messages_and_media.message import Message
from pyrogram.errors import UserAlreadyParticipant, PhoneNumberInvalid, FloodWait


INVITE_LINK_RE = Client.__dict__["INVITE_LINK_RE"]

string = """
> Session no encontrada.
Por favor siga los pasos de Pyrogram para autorizar su cuenta de Telegram.            
Pyrogram le pedirá su número telefonico. El número debe estar en formato internacional 
por ejemplo, para españa debe incluir el +34
"""


class MessagePlus:
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

    @property
    def message(self) -> Message:
        if getattr(self, "_message", None) is None:
            self._message = telegram.client.get_messages(
                self.chat_id, self.message_id)
        return self._message


class Config:
    def __init__(self):
        self._path = "config.yaml"
        config = self._load_file_config()

        self.api_hash = config["api_hash"]
        self.api_id = config["api_id"]
        self.chat_id = config["chat_id"]
        self.session_string = config.get("session_string", None)

    @property
    def path(self):
        return self._path

    def to_json(self):
        return attributes_to_json(self)

    def _create_file_config(self):
        json_data = {"API_HASH": "e0n7bf4d",
                     "API_ID": 1585711,
                     "CHAT_ID": "https://t.me/+Fz1aDRT"}
        with open(self.path, "wt") as fb:
            yaml.dump(json_data, fb, sort_keys=False)
    def _load_file_config(self):
        if not os.path.exists(self.path):
            self._create_file_config()
            print("Para continuar es necesario rellenar los datos de config.yaml")
            exit()

        with open(self.path, "rt") as fb:
            config = yaml.load(fb, Loader=yaml.UnsafeLoader)

        if config == None:
            raise Warning("El archivo config está vacio.")

        config_to_lower = {}
        for key, value in config.items():
            config_to_lower[key.lower()] = value
        return config_to_lower

    def _save_file_config(self):
        json_data = self.to_json()

        config_to_upper = {}
        for key, value in json_data.items():
            config_to_upper[key.upper()] = value

        with open(self.path, "wt") as fb:
            yaml.dump(config_to_upper, fb, sort_keys=False)


class Telegram(Config):
    def __init__(self):
        super().__init__()

    def _get_client(self):
        if self.session_string == None:
            print(string)
            os.system("pause")
            print("Cargando...")

            client = Client("my_account", api_id=self.api_id,
                            api_hash=self.api_hash, in_memory=True)
            try:
                client.start()
                self.session_string = client.export_session_string()
                self._save_file_config()
                print(f"La session se guardo en {os.path.basename(self.path)}")
                return client
            except PhoneNumberInvalid:
                print("\n*Advertencia*\nEl número introducido es invalido")
                exit()

        client = Client("my_account", session_string=self.session_string)
        client.start()
        print("Cargando...")
        return client

    @property
    def client(self) -> Client:
        if getattr(self, "_client", False) == False:
            self._client = self._get_client()
        return self._client

    def update(self, path: str, caption: str, filename: str) -> Message:
        if caption == filename:
            caption = ""
        message = self.client.send_document(
            chat_id=self.chat_id,
            document=path,
            file_name=filename,
            caption=caption,
            progress=progress)
        return MessagePlus.from_message(message)

    def get_message(self, link: str) -> MessagePlus:
        chat_id = "-100" + link.split("/")[-2]
        iD = int(link.split("/")[-1])
        message = self.client.get_messages(chat_id, iD)
        return MessagePlus.from_message(message)

    def test_session(self):
        """
        Comprueba si el usuario está logeado en Telegram.
        """
        user = self.client.get_users("me")
        print(f"{user.username or user.first_name}", "¡está logeado!\n")

    def _join_group(self, invite_link):
        """
        Intenta entrar a un grupo
        """
        try:
            return self.client.join_chat(invite_link)
        except UserAlreadyParticipant as e:
            return self.client.get_chat(self.chat_id)
        except FloodWait as e:
            print("Pyrogram ha generado una espera.", e.MESSAGE)
            exit()

    def get_id_from_chat(self):
        """
        Parametro:
            chat_id (``int`` | ``str``):
                Identificador único (int) o nombre de usuario (str) del chat de destino.
                Identificador único para el chat de destino en forma de enlace *t.me/joinchat/*, identificador (int) 
                o username del canal/supergrupo de destino (en el formato @username).
        """
        match = INVITE_LINK_RE.match(self.chat_id)
        if match:
            self._join_group(self.chat_id)

        chat = self.client.get_chat(self.chat_id)
        return chat.id

    def test_chat_id(self):
        """
        Prueba si hace parte del grupo y prueba si tiene permisos para subir archivos.
        - Entra al grupo si chat_id es una invitación valida
        """
        match = INVITE_LINK_RE.match(self.chat_id)          
        if match:           
            chatMember=self._join_group(self.chat_id)            
            self.chat_id= chatMember.id
            self._save_file_config()
        else:
            chatMember = self.client.get_chat(self.chat_id)
            
        if getattr(chatMember, "id", False)==False:
            print(f"El usuario no hace parte de chat_id {self.chat_id}")
            exit()             
        if not chatMember.permissions.can_send_media_messages:
            print(f"No tienes permisos para subir archivos en chat_id {self.chat_id}")
            exit()


telegram = Telegram()
telegram.test_session()
telegram.test_chat_id()
