
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
            setattr(self, "_client", self._get_client())
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

    @classmethod
    def download(cls, message_plus: MessagePlus, path=None)->str:
        """descarga un archivo de Telegram

        Args:
            message_plus: objeto de la clase MessagePlus
            path: Una ruta personalizada para guardar el archivo. Si no está presente el archivo se descarga en la carpeta de trabajo.

        Returns:
            str: ruta completa de donde se descargo el archivo.
        """
        chat_id = message_plus.chat_id
        message_id = message_plus.message_id

        output = path or os.path.join(Config.worktable, message_plus.file_name)
        if not os.path.exists(output):
            message = cls.client.get_messages(chat_id, message_id)
            cls.client.download_media(message,
                                      file_name=output,
                                      progress=progress,
                                      progress_args=(message_plus.file_name,)
                                      )
        return output

    @classmethod
    def get_message(cls, messageplus: MessagePlus) -> MessagePlus:

        return cls.client.get_messages(int(str(cls.chat_id).replace("-", "-100")), messageplus.message_id)

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
                match= match.group()
            else:
                match= cls.chat_id                
                
            try:
                chatinfo = cls.client.get_chat(cls.chat_id) 
                cls.chat_id= chatinfo.id # parece get_chat devuelve el id en el formato de pyrogram
                Config.insert_or_update_field({"chat_id": cls.chat_id})
            except ChatIdInvalid:
                print("No se pudo obtener la info del chat_id. Asegurate que el chat_id este en el formato de pyrogram o que sea un enlace de invitación de Telegram")
                exit()                 
            
        else:
            chatinfo = cls.client.get_chat(cls.chat_id)
            
            
        if getattr(chatinfo, "id", False) is False:
            print(f"El usuario no hace parte de chat_id {cls.chat_id}")
            exit()
        if not chatinfo.permissions.can_send_media_messages:
            print(
                f"No tienes permisos para subir archivos en chat_id {cls.chat_id}")
            exit()
        print("CHAT_ID:", chatinfo.title)

    @classmethod
    def check_session(cls):
        """
        Comprueba si el usuario está logeado en Telegram.
        """
        user = cls.client.get_users("me")
        print(f"{user.username or user.first_name}", "¡está logeado!\n")
