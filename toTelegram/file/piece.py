from email.message import Message
from ..telegram import Messageplus
from ..functions import get_part_filepart


class Piece:
    def __init__(self, path, filename, size, message: Messageplus = None):
        self.path = path
        self.filename = filename
        self.size = size
        self.message = message

    def to_json(self):
        filedocument = self.__dict__.copy()
        filedocument["message"] =self.message.to_json() if type(self.message) == Messageplus else self.message
        return filedocument

    def to_fileyaml(self):
        filename= self.filename
        size= self.size
        message= self.message.to_json() if type(self.message) == Messageplus else self.message
        return {
            "filename":filename,
            "size": size,
            "message": message
        }

    @property
    def part(self) -> str:
        return get_part_filepart(self.path)
