import os
from typing import Union

from pyrogram import Client  # pip install pyrogram
from pyrogram.types.messages_and_media.message import Message
from pyrogram.types.messages_and_media.document import Document

from .temp import Temp, create_filedocument, split, load_md5document, check_md5document, create_md5document
from .constants import WORKTABLE
from .functions import get_md5sum, load_config


def progress(current, total):
    print(f"\t\t{current * 100 / total:.1f}%", end="\r")


def get_document(message: Union[Message, dict]) -> dict:
    # el filanem debe ser obtenido del message, no del temp. Esto es debido a que telegram o la api te pueden cambiar el nombre del archivo.
    if type(message) == Message:
        value = message.media.value
        document: Document = getattr(message, value)

        return {"filename": document.file_name, "message_id": message.id}
    elif type(message) == dict:
        value = message["media"]["value"]
        document: dict = message["value"]
        return {"filename": document["file_name"], "message_id": message["message_id"]}
    print(message, type(message))
    raise Exception("message no es un Message ni un dict")


class ToTelegram:
    def __init__(self):
        self.config = load_config()
        self.is_client_initialized = False
        self.username = self.config.get("USERNAME", "me")
        self.api_hash = self.config["API_HASH"]
        self.api_id = self.config["API_ID"]
        self.chat_id = self.config["CHAT_ID"]

    def _start(self):
        client = Client(self.username, self.api_id, self.api_hash)
        client.start()
        return client

    @property
    def client(self) -> Client:
        if not self.is_client_initialized:
            self._client = self._start()
            self.is_client_initialized = True
        return self._client

    def update(self, path, md5sum=None, **kwargs):
        if check_md5document(md5sum):
            md5document = load_md5document(md5sum)
        else:
            md5document = create_md5document(path, md5sum)
        temp = Temp(md5document)
        print("md5sum:", temp.md5sum)

        if not temp.is_complete_filedocument:
            if not temp.is_split_required:
                filename = os.path.basename(path)
                message = self.client.send_document(
                    self.chat_id, path, file_name=filename, caption=filename, progress=progress)
                filedocument = create_filedocument(message)
                temp.add_filedocument(filedocument)
            else:
                verbose = kwargs.get("verbose", True)
                for filepart in split(temp, verbose):
                    if not temp.check_filepart(filepart):
                        filename = os.path.basename(filepart)
                        path = os.path.join(WORKTABLE, filepart)
                        message = self.client.send_document(
                            self.chat_id, path, file_name=filename, progress=progress)
                        filedocument = create_filedocument(message)
                        temp.add_filedocument(filedocument)
            temp.create_fileyaml(self.chat_id)
        else:
            print("El archivo ya está completo")

    def get_message(self, link) -> Message:
        iD = link.split("/")[-1]
        return self.client.get_messages(self.chat_id, int(iD))

    def create_fileyaml(self, path, md5sum,URLs ):
        if check_md5document(md5sum):
            md5document = load_md5document(md5sum)
        else:
            md5document = create_md5document(path, md5sum)
        temp = Temp(md5document)
        if temp.count_parts != len(URLs):
            print("El número de partes no coincide con el número de URLs")
            return
        temp.md5document["fileparts"] = []
        for url in URLs:
            message = self.get_message(url)
            filedocument = create_filedocument(message)
            temp.add_filedocument(filedocument)
