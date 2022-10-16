
import os
from typing import List, Union

from ..file.piecesfile import Piecesfile
from ..file.singlefile import Singlefile
from ..telegram import telegram
from .multifile import Multifile


class Backup:
    def __init__(self,
                 file: Union[Piecesfile, Singlefile],
                 files: List[Multifile],
                 ):
        self.file = file
        self.files = files

    def update(self, remove=False):
        if self.file.type == "single-file":
            path = self.file.path
            caption = self.file.filename
            filename = self.file.filename_for_telegram
            message = telegram.update(
                path, caption=caption, filename=filename)
            if remove:
                os.remove(self.file.path)
            self.file.message = message
        else:
            raise Exception(
                "Solo se puede usar en Backup con file type singlefile")

    def to_json(self):
        document = self.__dict__.copy()
        document["file"] = self.file.to_json()
        document["files"] = [file.to_json() for file in self.files]
        return document
