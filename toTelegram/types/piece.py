import os

from ..utils import get_part_filepart, attributes_to_json
from ..config import Config
from .messageplus import MessagePlus
from ..telegram import Telegram


class Piece:
    def __init__(
        self,
        filename,
        size,
        telegram: Telegram,
        message=None,
        kind=None,
    ):
        self.kind = kind or "#piece"
        self.filename = filename
        self.size = size
        self.message = message
        self.telegram = telegram

    @property
    def path(self):
        return os.path.join(self.telegram.config.path_chunk, self.filename)

    @property
    def part(self) -> str:
        return get_part_filepart(self.path)

    @property
    def parts(self):
        """
        Calcula todas las partes que conforman el archivo de esta pieza.
        Devuelve una lista de path que apuntan a la carpeta de trabajo.
        """
        string_parts = self.filename.split("_")
        total_parts = int(string_parts[-1].split("-")[1])
        name = string_parts[0]

        index = 1
        paths = []
        while index <= total_parts:
            new_name = name + f"_{index}-{total_parts}"
            index += 1
            paths.append(os.path.join(self.telegram.config.worktable, new_name))
        return paths

    def to_json(self):
        return attributes_to_json(self)

    @classmethod
    def from_path(cls, path, message=None):
        filename = os.path.basename(path)
        size = os.path.getsize(path)
        return Piece(filename=filename, size=size, message=message)

    @classmethod
    def from_json(cls, json_data):

        if isinstance(json_data["message"], dict):
            json_data["message"] = MessagePlus(**json_data["message"])
        elif isinstance(json_data["message"], MessagePlus):
            pass
        else:
            raise KeyError("")

        return Piece(
            kind=json_data["kind"],
            filename=json_data["filename"],
            size=json_data["size"],
            message=json_data["message"],
        )
