import os
from typing import Union, Dict

from pyrogram import Client  # pip install pyrogram
from pyrogram.types.messages_and_media.message import Message
from pyrogram.types.messages_and_media.document import Document

from .temp import Temp, split, load_md5document, check_md5document, create_md5document
from .constants import WORKTABLE
from .functions import get_md5sum, load_config, filepath, get_part_filepart,create_filedocument


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

        md5sum = md5sum if md5sum else get_md5sum(path)
        if check_md5document(md5sum):
            md5document = load_md5document(md5sum)
        else:
            md5document = create_md5document(path, md5sum)
        temp = Temp(md5document)
        print("md5sum:", temp.md5sum)

        if not temp.is_complete_filedocument:
            if not temp.is_split_required:
                filename = os.path.basename(path)
                print("\n\t", filename)
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
                        temp.remove_local_filepart(filepart)
            temp.create_fileyaml(self.chat_id)
        else:
            print("El archivo ya está completo")

    def get_message(self, link) -> Message:
        iD = link.split("/")[-1]
        return self.client.get_messages(self.chat_id, int(iD))

    def create_fileyaml(self, path, md5sum, URLs):
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

    def download(self, target: Union[str, list], **kwargs):
        # TODO: comprobar si el archivo ya ha sido descargado.
        output = kwargs.get("output", "")

        if type(target) == str:
            target = filepath(target)
        else:
            messages = [self.get_message(link) for link in target]
            messagedocuments = [create_messagedocument(
                message) for message in messages]
            filename_object: dict = organize_messagedocuments(messagedocuments)
            checked_filename_object: dict = check_filename_object(
                filename_object)

            good_filename_object: dict = checked_filename_object["good"]
            for filename, parts_dict in good_filename_object.items():
                parts = list(parts_dict.keys())
                # absolute_paths = [self.client.download_media(
                #     parts_dict[part]["message"], progress=progress) for part in parts]
                absolute_paths = []
                for part in parts:
                    filepart = parts_dict[part]["filepart"]
                    message = parts_dict[part]["message"]
                    path = os.path.join(WORKTABLE, filepart)
                    size = parts_dict[part]["size"]

                    if os.path.exists(path) and os.path.getsize(path) == size:
                        absolute_paths.append(path)
                        continue

                    absolute_paths.append(self.client.download_media(
                        message, path, progress=progress))

                path = os.path.join(output, filename)
                with open(path, "wb") as f:
                    for absolute_path in absolute_paths:
                        with open(absolute_path, "rb") as p:
                            for chunk in iter(lambda: p.read(1024 * 8), b''):
                                f.write(chunk)
            good_filename_object: dict = checked_filename_object["bad"]
            for filename, parts_dict in good_filename_object.items():
                for item in parts_dict.keys():
                    url = parts_dict[item]["url"]
                    print(f"\n\t{url}")


def check_filename_object(filename_object: dict) -> bool:
    filename_good = []
    filename_bad = []
    for item in filename_object:
        key, value = next(iter(filename_object.items()))
        count_parts = value["count_parts"]
        if count_parts > 1:
            parts = list(value.keys())
            total_parts = [str(part) + "-" + str(count_parts)
                           for part in range(1, count_parts+1)]
            check = all(part in parts for part in total_parts)
            if check == True:
                filename_good.append(item)
            else:
                filename_bad.append(item)
        else:
            if item.get(None):
                filename_good.append(item)
            else:
                filename_bad.append(item)
    return {"good": filename_good, "bad": filename_bad}

def organize_messagedocuments(messagedocuments: list) -> Dict:
    filename_objects = {}
    for messagedocument in messagedocuments:
        filename = messagedocument["filename"]
        part = messagedocument["part"]
        message = messagedocument["message"]
        url = messagedocument["url"]
        count_parts = messagedocument["count_parts"]

        value = {part: {"message": message,
                        "url": url, "count_parts": count_parts}}
        if filename_objects.get(filename):
            filename_objects[filename].update(value)
        else:
            filename_objects[filename] = value
    return filename_objects


def create_messagedocument(message: Message, url: str):
    filedocument = get_document(message)
    filepart = filedocument["filename"]
    part = get_part_filepart(filepart)
    filename = filedocument["filename"].replace("_"+part, "")
    count_parts = int(part.split("-")[1]) if type(part) == str else 1
    return {
        "filename": filename,
        "part": part,
        "message": message,
        "url": url,
        "count_parts": count_parts,
        "filepart": filepart
    }


def progress(current, total):
    print(f"\t\t{current * 100 / total:.1f}%", end="\r")


def get_document(message: Union[Message, dict]) -> dict:
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
