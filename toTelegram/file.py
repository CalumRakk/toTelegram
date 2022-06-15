from __future__ import annotations
import os.path
import math
import subprocess
from typing import TYPE_CHECKING, List

import yaml
from yaml.reader import ReaderError

from .functions import check_md5sum, get_md5sum, check_md5sum, get_md5sum
from .constants import FILESIZE_LIMIT, WORKTABLE, EXT_YAML, VERSION, REGEX_FILEPART_OF_STRING
from .telegram import Telegram, TELEGRAM
from .messageplus import Messageplus
if TYPE_CHECKING:
    from .chunk import Chunk


class File:
    def __init__(self, path: str, md5sum: str = None) -> None:
        self.md5sum = check_md5sum(md5sum, None) or get_md5sum(path)
        object_ = self._load()
        if object_ is None:
            self.is_split_finalized = False
            self.is_upload_finished = False

            self.path = os.path.abspath(path)
            self.filename = os.path.basename(path)
            self.size = os.path.getsize(path)
            self.exceed_file_size_limit = self.size > FILESIZE_LIMIT
            self.count_part = math.ceil(self.size / FILESIZE_LIMIT)
            self.messages = []
            self.chunks = []
            self.save()
        else:
            self.__dict__ = object_.__dict__
        # debe ir de último sino, no se añade.
        self.telegram: Telegram = TELEGRAM

    def __getstate__(self):
        # Usado por YAML para serializar el objeto
        state = self.__dict__.copy()
        if state.get("telegram"):
            del state["telegram"]
        return state

    def __repr__(self) -> str:
        string = f'''
            md5sum: {self.md5sum},
            version: {VERSION},
            files: {self.messages}
        '''
        return string

    def _load(self) -> object:
        path = os.path.join(WORKTABLE, self.md5sum + EXT_YAML)

        if not os.path.exists(path):
            return None

        try:
            with open(path, 'rb') as f:
                document_temp = yaml.load(f, Loader=yaml.UnsafeLoader)
            return document_temp

        except ReaderError:
            print("Error al leer el archivo temporal: " + path)
            exit()

    def save(self):
        path = os.path.join(WORKTABLE, self.md5sum+EXT_YAML)
        # dictt= self.__dict__.copy()
        # dictt.pop("telegram")
        with open(path, "w") as file:
            yaml.dump(self, file)

    def update(self) -> None:
        message = self.telegram.update(self.path, caption=True)
        message = Messageplus(message)
        self.messages.append(message)
        self.is_upload_finished = True
        self.save()

    def split(self) -> List[Chunk]:
        """
        Divide el archivo en partes si supera el limite de Telegram
        """
        # TODO: volver process asíncrono para que devuelve las partes que va dividiendo.
        print("[Split]...\n")

        name = self.filename + "_"
        output = os.path.join(WORKTABLE, name)
        digits = len(str(self.count_part))

        cmd = f'split "{self.path}" -b {FILESIZE_LIMIT} -d --verbose --suffix-length={digits} --numeric-suffixes=1 --additional-suffix=-{self.count_part} "{output}"'

        completedProcess = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if completedProcess.returncode == 1:
            print(completedProcess.stderr.decode())
            raise

        fileparts = []  # lista de rutas completas
        for text in completedProcess.stdout.decode().split("\n")[:-1]:
            filepart = REGEX_FILEPART_OF_STRING.search(text).group()
            basename = os.path.basename(filepart)
            print(f"\t{basename}")
            fileparts.append(basename)

        from .chunk import Chunk
        chunks = []
        for filepart in fileparts:
            chunk = Chunk(os.path.join(WORKTABLE, filepart), self)
            chunks.append(chunk)
        self.chunks = chunks
        self.is_split_finalized = True
        self.save()

    def create_fileyaml(self):
        document = {
            "md5sum": self.md5sum,
            "version": VERSION,
            "files": self.messages
        }
        dirname = os.path.dirname(self.path)
        ext = os.path.splitext(self.path)[1]
        name = self.filename.replace(ext, "") + EXT_YAML
        path = os.path.join(dirname, name)
        with open(path, "w") as file:
            yaml.dump(document, file)
