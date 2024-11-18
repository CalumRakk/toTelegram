import json
import os
import lzma
from typing import List, Union
from pathlib import Path

from ..telegram import MessagePlus, Telegram
from ..types.file import File
from ..utils import attributes_to_json, is_filename_too_long, TemplateSnapshot
from .. import constants
from ..config import Config
from ..types.piece import Piece
from ..filechunker import FileChunker


# def sort_parts(worktable, parts: Union[list, str]):
#     if isinstance(parts, list):
#         anchor = parts[0]
#     else:
#         anchor = parts

#     string_parts = anchor.split("_")
#     total_parts = int(string_parts[-1].split("-")[1])
#     name = string_parts[0]

#     index = 1
#     paths = []
#     while index <= total_parts:
#         new_name = name + f"_{index}-{total_parts}"
#         index += 1
#         paths.append(os.path.join(worktable, new_name))
#     return paths


class PiecesFile:
    def __init__(self, kind=None, file: File = None, pieces=None, telegram=Telegram):
        self.kind = kind or "pieces-file"
        self.file = file
        self.pieces = pieces
        self.telegram = telegram

    @property
    def is_split_finalized(self):
        """
        True si el archivo ha sido dividido en piezas
        y si sus piezas existen localmente para poder ser usadas.
        """
        if bool(self.pieces) is False:
            return False
        for piece in self.pieces:
            if piece.message is None and not os.path.exists(piece.path):
                return False
        return True

    @property
    def is_finalized(self):
        """
        True si todas las piezas han sido subido.
        False si el atributo pieces es una lista vacia o alguna pieza no ha sido subido.
        """
        if self.pieces:
            for piece in self.pieces:
                if bool(piece.message) is False:
                    return False
            return True
        return False

    def to_json(self):
        return attributes_to_json(self)

    def update(self):
        """
        Sube el archivo a Telegram
        """
        if not self.is_split_finalized:
            self.pieces = self.split()
            self.save()

        print("\t[UPDATE]")
        for piece in self.pieces:
            if piece.message is None:
                caption = piece.filename
                filename = piece.filename

                if is_filename_too_long(filename):
                    filename = self.file.md5sum + os.path.splitext(filename)[1]

                piece.message = self.telegram.update(
                    piece.path, caption=caption, filename=filename
                )
                os.remove(piece.path)
                self.save()
                continue
            print("\t", piece.filename, "DONE.")
        os.remove(os.path.join(self.telegram.config.worktable, self.file.md5sum))

    def download(self, path: Path):
        folder = path.parent
        path = folder / self.file.filename

        if path.exists():
            return True

        paths = []
        for piece in self.pieces:
            path_piece = os.path.join(
                self.telegram.config.worktable, piece.message.file_name
            )
            if not os.path.exists(path_piece):
                path_piece = self.telegram.download(piece.message)
            paths.append(path_piece)

        filechunker = FileChunker()
        files = [Path(file) for file in paths]
        output_file = folder / self.file.filename
        path = filechunker.concatenate_files(files=files, output=output_file)

        for i in paths:
            os.remove(i)
        print(path)

    def save(self):
        path = os.path.join(self.telegram.config.worktable, self.file.md5sum)
        json_data = self.to_json()
        json_data["verion"] = constants.VERSION

        with open(path, "w", encoding="utf-8") as file:
            json.dump(json_data, file)

    def split(self) -> List[Piece]:
        """
        Divide el archivo en partes al limite de Telegram
        """
        from ..filechunker import FileChunker

        print("\t[SPLIT]")
        # split = Split(self.file.path)
        # output = os.path.join(config.path_chunk, self.file.filename)
        # fileparts = split(chunk_size=constants.FILESIZE_LIMIT, output=output)

        fileparts = FileChunker().split_file(
            path=self.file.path,
            output=self.telegram.config.path_chunk,
            chunk_size=constants.FILESIZE_LIMIT,
        )

        pieces = []
        for path in fileparts:
            piece = Piece.from_path(path)
            pieces.append(piece)
        return pieces

    def create_snapshot(self):
        template = TemplateSnapshot(self)

        dirname = os.path.dirname(self.file.path)
        filename = os.path.basename(self.file.path)
        path = os.path.join(dirname, filename + constants.EXT_JSON_XZ)

        with lzma.open(path, "wt") as f:
            json.dump(template.to_json(), f)

    @classmethod
    def from_file(cls, file: File, telegram: Telegram = None):
        """
        Devuelve una instancia de PiecesFile que conserva el valor de .path
        """

        cache_pieces = os.path.join(telegram.config.worktable, file.md5sum)

        if os.path.exists(cache_pieces):
            with open(cache_pieces, "r", encoding="utf-8") as f:
                json_data = json.load(f)
            pieces = []
            for doc in json_data["pieces"]:
                doc["message"] = (
                    MessagePlus(**doc["message"]) if doc["message"] else None
                )
                pieces.append(Piece(**doc, telegram=telegram))
            return PiecesFile(file=file, pieces=pieces, telegram=telegram)

        return PiecesFile(file=file, pieces=None, telegram=telegram)

    @classmethod
    def from_json(cls, json_data):
        if isinstance(json_data["file"], dict):
            json_data["file"] = File(**json_data["file"])
        elif isinstance(json_data["file"], File):
            pass
        else:
            raise KeyError("")

        pieces = []
        for piece in json_data["pieces"]:
            if isinstance(piece, dict):
                pieces.append(Piece.from_json(piece))
            elif isinstance(piece, Piece):
                pieces.append(piece)
            else:
                raise KeyError("")

        json_data["pieces"] = pieces

        return PiecesFile(**json_data)
