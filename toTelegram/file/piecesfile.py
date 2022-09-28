import subprocess
import math
import os
import json
from typing import List, Union
from pathlib import Path
import asyncio

import yaml

from ..constants import FILESIZE_LIMIT, WORKTABLE, REGEX_FILEPART_OF_STRING, EXT_JSON, EXT_YAML,VERSION
from .singlefile import Singlefile
from ..telegram import Messageplus
from .file import File


class Piecesfile:
    def __init__(self,
                 file: File,
                 pieces: List[Singlefile]=[]
                 ):
        self.file = file
        self.pieces = pieces
    @property
    def path(self):
        return self.file.path
    @property
    def type(self):
        return self.file.type
    @property
    def is_split_finalized(self):
        """
        True si el archivo ha sido dividido en piezas.
        """
        if self.pieces:
            return True
        return False

    @property
    def is_finalized(self):
        """
        True si todas las piezas han sido subido.
        False si el atributo pieces es una lista vacia o alguna pieza no ha sido subido.
        """
        if self.pieces:
            for piece in self.pieces:
                if bool(piece.message) == False:
                    return False
            return True
        return False

    def save(self,version=True):
        filename = self.file.inodo_name + EXT_JSON
        path = os.path.join(WORKTABLE, filename)
        document= self.__dict__.copy()
        if version:            
            document["version"]= VERSION 
        with open(path, "w") as file:
            json.dump(document,file, default= lambda x: x.to_json())

    def to_json(self):
        document = self.__dict__.copy()
        document["file"]= self.file.to_json()
        document["pieces"] = [piece.to_json() for piece in self.pieces]
        return document

    def load(self):
        """
        
        """
        json_data = self.file._load()
        pieces = []
        if json_data:
            for document in json_data["pieces"]:
                file= File(**document["file"])
                message= Messageplus(**document["message"]) if document["message"] else None
                pieces.append(Singlefile(file, message))
        self.pieces= pieces
        return self

    def split(self) -> List[Singlefile]:
        """
        Divide el archivo en partes al limite de Telegram
        """
        # TODO: volver process asíncrono para que devuelve las partes que va dividiendo.
        print("[Split]...\n")

        # name = self.file.md5sum + self.file.suffix + "_"
        # if not check_file_name_length(self.file.path):
        #     name = self.file.filename + "_"
        name = self.file.filename + "_"
        output = os.path.join(WORKTABLE, name)
        count_part = math.ceil(self.file.size / FILESIZE_LIMIT)
        digits = len(str(count_part))

        cmd = f'split "{self.file.path}" -b {FILESIZE_LIMIT} -d --verbose --suffix-length={digits} --numeric-suffixes=1 --additional-suffix=-{count_part} "{output}"'

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

        pieces = []
        for filepart in fileparts:
            path = os.path.join(WORKTABLE, filepart)
            singlefile = Singlefile(File(path,md5sum=False))
            pieces.append(singlefile)
        self.pieces=pieces

    def to_fileyaml(self):
        filename = self.file.filename
        md5sum = self.file.md5sum
        size = self.file.size
        type = self.file.type
        version = self.file.version
        pieces = [piece.to_fileyaml() for piece in self.pieces]
        return {
            "filename": filename,
            "md5sum": md5sum,
            "size": size,
            "pieces": pieces,
            "type": type,
            "version": version
        }

    def create_fileyaml(self, path: Union[str, Path]):
        # json_data = self.to_fileyaml()
        filename = os.path.basename(self.file.filename)
        dirname = os.path.dirname(self.file.path)
        ext = getattr(path, "suffix", None) or os.path.splitext(path)[1]

        name = filename.replace(ext, "") + EXT_YAML
        path = os.path.join(dirname, name)
        with open(path, "w") as file:
            yaml.dump(self.to_json(), file, sort_keys=False)
