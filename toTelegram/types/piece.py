
import os

from ..functions import get_part_filepart, attributes_to_json
from ..config import Config


class Piece:
    def __init__(self, filename, size, message=None, kind=None):
        self.kind = kind or "#piece"
        self.filename = filename
        self.size = size
        self.message = message

    @property
    def path(self):
        return os.path.join(Config.path_chunk, self.filename)

    @property
    def part(self) -> str:
        return get_part_filepart(self.path)

    def to_json(self):
        return attributes_to_json(self)

    @classmethod
    def from_path(cls, path, message=None):
        filename = os.path.basename(path)
        size = os.path.getsize(path)
        return Piece(filename=filename, size=size, message=message)
