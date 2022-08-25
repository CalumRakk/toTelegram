import subprocess
import math
import os
import json
from typing import List, Union
from pathlib import Path

import yaml

from ..constants import FILESIZE_LIMIT, WORKTABLE, REGEX_FILEPART_OF_STRING, EXT_JSON, EXT_YAML
from .piece import Piece
from .file import File
from ..telegram import Telegram, Messageplus, telegram


class Piecesfile:
    def __init__(self, file: File) -> None:
        self.file = file
        self.pieces = self._load_pieces(telegram)

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

    def save(self):
        filename = str(self.file.dev) + "-" + str(self.file.inodo) + EXT_JSON
        path = os.path.join(WORKTABLE, filename)
        json_data = self.to_json()

        with open(path, "w") as file:
            json.dump(json_data, file)

    def to_json(self):
        filedocument = self.file.to_json()
        filedocument["pieces"] = [piece.to_json() for piece in self.pieces]
        return filedocument

    def _load_pieces(self, telegram: Telegram):
        json_data = self.file._load_file()
        pieces = []
        if json_data:
            json_pieces: dict = json_data["pieces"]
            for json_piece in json_pieces:
                path = json_piece["path"]
                filename = json_piece["filename"]
                size = json_piece["size"]
                md5sum= json_piece["md5sum"]
                message = None

                json_message = json_piece["message"]
                if json_message:
                    link = json_message["link"]
                    message = telegram.get_message(link)

                piece = Piece(path=path, filename=filename,
                              size=size, md5sum=md5sum, message=message)
                pieces.append(piece)
        return pieces

    def split(self) -> List[Piece]:
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
            size = os.path.getsize(path)
            piece = Piece(path=path, filename=filepart,
                          size=size, md5sum=self.file.md5sum)
            pieces.append(piece)
        return pieces

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
        json_data = self.to_fileyaml()
        filename = getattr(path, "name", None) or os.path.basename(path)
        dirname = str(getattr(path, "parent", None)) or os.path.dirname(path)
        ext = getattr(path, "suffix", None) or os.path.splitext(path)[1]

        name = filename.replace(ext, "") + EXT_YAML
        path = os.path.join(dirname, name)
        with open(path, "w") as file:
            yaml.dump(json_data, file, sort_keys=False)
