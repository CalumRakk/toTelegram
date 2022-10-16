import os
from email.message import Message

from ..functions import check_file_name_length, get_part_filepart
from ..telegram import Messageplus


class Piece:
    def __init__(self, path, filename, size, md5sum, message: Messageplus = None):
        self.path = path
        self.filename = filename
        self.size = size
        self.message = message
        self.md5sum= md5sum
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
    def filename_for_telegram(self):
        if check_file_name_length(self.filename):        
            return self.filename
        suffix= os.path.splitext(self.filename)[1]
        return  self.md5sum + suffix 
    @property
    def part(self) -> str:
        return get_part_filepart(self.path)
