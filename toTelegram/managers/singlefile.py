import json
import os
import lzma
from pathlib import Path

from ..constants import EXT_JSON_XZ
from ..utils import is_filename_too_long, attributes_to_json, TemplateSnapshot
from ..telegram import Telegram
from ..types.file import File
from ..types.messageplus import MessagePlus


class SingleFile:
    def __init__(self, telegram: Telegram, file: File, message=None, kind=None):
        self.kind = kind or "single-file"
        self.file = file
        self.message = message
        self.telegram = telegram

    def update(self, remove=False):
        """
        remove: True para eliminar el archivo una vez se sube a telegram.
        Enviar el archivo a telegram y añade el resultado (message) a la propiedad self.message
        """
        caption = self.filename
        filename = self.filename_for_telegram
        if is_filename_too_long(filename):
            filename = self.file.md5sum + os.path.splitext(filename)[1]
        path = self.path
        self.message = self.telegram.update(path, caption=caption, filename=filename)
        if remove:
            os.remove(self.path)

    def download(self, path: Path):
        path = path / self.file.filename
        if os.path.exists(path):
            return True

        self.telegram.download(self.message, path=path)
        print(path)

    def to_json(self):
        return attributes_to_json(self)

    def create_snapshot(self):
        template = TemplateSnapshot(self)

        dirname = os.path.dirname(self.file.path)
        filename = os.path.basename(self.file.path)
        path = os.path.join(dirname, filename + EXT_JSON_XZ)

        with lzma.open(path, "wt") as f:
            json.dump(template.to_json(), f)

    @property
    def type(self):
        return self.file.type

    @property
    def filename(self):
        return self.file.filename

    @property
    def path(self):
        return self.file.path

    @property
    def filename_for_telegram(self):
        if is_filename_too_long(self.file.path):
            return self.file.filename
        return self.file.md5sum + os.path.splitext(self.file.filename)[1]

    @classmethod
    def from_json(cls, json_data):
        if isinstance(json_data["file"], dict):
            json_data["file"] = File(**json_data["file"])
        elif isinstance(json_data["file"], File):
            pass
        else:
            raise KeyError("")

        if isinstance(json_data["message"], dict):
            json_data["message"] = MessagePlus(**json_data["message"])
        elif isinstance(json_data["message"], MessagePlus):
            pass
        else:
            raise KeyError("")

        return SingleFile(**json_data)
