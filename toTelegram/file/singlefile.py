from typing import Union
from pathlib import Path
import os
import yaml
import json

from .file import File
from ..telegram import Messageplus, Telegram, telegram
from ..constants import WORKTABLE, EXT_JSON, EXT_YAML
from ..functions import check_file_name_length


class Singlefile:
    def __init__(self, file: File):
        self.file = file
        self.type = "single-file"
        self.message = self._load_message(telegram)

    @property
    def filename_for_telegram(self):
        if check_file_name_length(self.file.path):
            return self.file.filename
        return self.file.md5sum + self.file.suffix

    @property
    def is_finalized(self):
        """
        True si todas las piezas han sido subido.
        False si el atributo pieces es una lista vacia o alguna pieza no ha sido subido.
        """
        if self.message:
            return True
        return False

    def _load_message(self, telegram: Telegram):
        json_data = self.file._load_file()
        message = None
        if json_data:
            json_message: dict = json_data["message"]
            if json_message:
                link = json_message["link"]
                message = telegram.get_message(link)
        return message

    def save(self):
        filename = str(self.file.dev) + "-" + str(self.file.inodo) + EXT_JSON
        path = os.path.join(WORKTABLE, filename)
        json_data = self.to_json()

        with open(path, "w") as file:
            json.dump(json_data, file)

    def to_json(self):
        filedocument = self.file.to_json()
        filedocument["message"] = None if self.message is None else self.message.to_json()
        return filedocument

    def to_fileyaml(self):
        filename = self.file.filename
        md5sum = self.file.md5sum
        size = self.file.size
        type = self.file.type
        version = self.file.version
        message = None if self.message is None else self.message.to_json()
        return {
            "filename": filename,
            "md5sum": md5sum,
            "size": size,
            "message": message,
            "type": type,
            "version": version
        }

    def create_fileyaml(self, path: Union[str, Path]):
        json_data = self.to_fileyaml()
        filename = getattr(path, "name", None) or os.path.basename(path)
        dirname = str(getattr(path, "parent", None)) or os.path.dirname(path)
        ext = getattr(path, "suffix", None) or os.path.splitext(path)[1]

        name = filename.replace(ext, "") + EXT_YAML
        path = os.path.join(dirname, name)
        with open(path, "w") as file:
            yaml.dump(json_data, file, sort_keys=False)
