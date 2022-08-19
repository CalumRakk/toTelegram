import os.path
from typing import List

from .telegram.messageplus import Messageplus
from .constants import WORKTABLE


class File_Online:

    def __init__(self, file: Messageplus, parts: List[Messageplus]) -> None:
        self.filename = file.filename
        self.file_name = file.file_name
        self.link = file.link
        self._file = file
        self._messages = parts

    @property
    def parts(self) -> List[Messageplus]:
        if getattr(self, "_parts", None) is None:
            parts = []
            for message in self._messages:
                if message.filename == self.filename:
                    parts.append(message)
            self._parts = parts
        return self._parts

    @property
    def is_complete(self) -> bool:
        """
        True si si todos los messages componen un archivo completo, False en caso contrario o si hay menos de 1 message
        """
        if len(self._messages) < 1:
            return False
        if self._file.part == "":
            return False

        count_part = int(self._file.part.split("-")[1])
        total_parts = [str(part) + "-" + str(count_part)
                       for part in range(1, count_part+1)]
        parts = [message.part for message in self.parts]
        return all(part in parts for part in total_parts)

    @property
    def type(self) -> str:
        if self._file.part == "":
            return "unsplit"
        return "split"

    def download(self) -> List[str]:
        if self.type == "split":
            paths = []
            for message in self.parts:
                size = message.size
                filename = message.filename
                path = os.path.join(WORKTABLE, filename)
                if os.path.exists(path) and os.path.getsize(path) == size:
                    paths.append(path)
                    continue
                paths.append(message.download())
            return paths
        return self._file.download()
