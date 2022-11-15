
import os

from ..functions import get_part_filepart, is_filename_too_long,attributes_to_json
from ..config import Config

class Piece(Config):
    # @classmethod
    # def from_json(cls, json_data):
    #     json_data["message"] = MessagePlus(**json_data["message"])
    #     return Piece(**json_data)

    @classmethod
    def from_path(cls, path, message=None):
        filename = os.path.basename(path)
        size = os.path.getsize(path)
        return Piece(filename=filename, size=size, message=message)

    def __init__(self, filename, size, message=None, kind=None):
        self.kind = kind or "#piece"
        self.filename = filename
        self.size = size
        self.message = message
    
    @property
    def path(self):
        return os.path.join(self.path_chunk, self.filename)
    @property
    def part(self) -> str:
        return get_part_filepart(self.path)

    def to_json(self):
        return attributes_to_json(self)
