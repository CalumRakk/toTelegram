from __future__ import annotations
import os
from typing import TYPE_CHECKING

from .functions import get_part_filepart
if TYPE_CHECKING:
    from .file import File
from .messageplus import Messageplus


class Chunk:
    def __init__(self, path, parent: File):
        self.path = path
        self.file_name: str = os.path.basename(path)
        self.parent = parent

        self.part = get_part_filepart(self.filepart)

    @property
    def filename(self):
        return self.file_name.replace("_"+self.part, "")

    @property
    def filepart(self):
        return getattr(self, "file_name", os.path.basename(self.path))

    @property
    def is_online(self):
        def is_online():
            for message in self.parent.messages:
                if message.file_name == getattr(self, "file_name", self.filepart):
                    print(f"\t is_online:{self.filepart}")
                    return True
            return False
        return is_online()

    def update(self):
        print(getattr(self, "file_name", self.filepart))
        message = self.parent.telegram.update(self.path, caption=False)
        print()
        self.parent.messages.append(Messageplus(message))
        self.parent.save()
    def remove(self):
        os.remove(self.path)
