
import json
import os
import json
import lzma
from typing import List


from ..telegram import MessagePlus
from ..file import File
from ..functions import attributes_to_json, check_file_name_length, get_part_filepart, TemplateSnapshot
from ..split import Split
from ..constants import (EXT_JSON_XZ, FILESIZE_LIMIT,
                        VERSION)
from ..config import Config
from ..telegram import Telegram

class Piece:
    # @classmethod
    # def from_json(cls, json_data):
    #     json_data["message"] = MessagePlus(**json_data["message"])
    #     return Piece(**json_data)

    @classmethod
    def from_path(cls, path, message=None):
        filename = os.path.basename(path)
        size = os.path.getsize(path)
        return Piece(filename=filename, size=size, message=message)

    def __init__(self, filename, size, message=None, kind=None):
        self.kind = kind or "#piece"
        self.filename = filename
        self.size = size
        self.message = message
    
    @property
    def path(self):
        return os.path.join(Config.path_chunk, self.filename)
    @property
    def filename_for_telegram(self):
        if check_file_name_length(self.filename):
            return self.filename
        suffix = os.path.splitext(self.filename)[1]
        return self.md5sum + suffix

    @property
    def part(self) -> str:
        return get_part_filepart(self.path)

    def to_json(self):
        return attributes_to_json(self)


class PiecesFile:
    config= Config()
    telegram= Telegram()    
    def __init__(self, kind=None, file: File = None, pieces=None):
        self.kind = kind or "pieces-file"
        self.file = file
        self.pieces = pieces
    
    @property
    def is_split_finalized(self):
        """
        True si el archivo ha sido dividido en piezas y si sus piezas existen localmente para poder ser usadas. 
        """
        if bool(self.pieces) == False:
            return False
        for piece in self.pieces:
            if piece.message == None and not os.path.exists(piece.path):
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
                if bool(piece.message) == False:
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
            if piece.message == None:
                caption = piece.filename
                filename = piece.filename
                                
                if not check_file_name_length(filename):
                    filename= self.file.md5sum + os.path.splitext(filename)[1]                

                piece.message = self.telegram.update(
                    piece.path, caption=caption, filename=filename)
                os.remove(piece.path)
                self.save()
                continue
            print("\t", piece.filename, "DONE.")

    def save(self):
        path = os.path.join(self.config.worktable, self.file.md5sum)
        json_data = self.to_json()
        json_data["verion"] = VERSION

        with open(path, "w") as file:
            json.dump(json_data, file)

    def split(self) -> List[Piece]:
        """
        Divide el archivo en partes al limite de Telegram
        """
        print("\t[SPLIT]")
        split = Split(self.file.path)
        output = os.path.join(Config.path_chunk, self.file.filename)
        fileparts = split(chunk_size=FILESIZE_LIMIT, output=output)

        pieces = []
        for path in fileparts:
            piece = Piece.from_path(path)
            pieces.append(piece)
        return pieces
    
    def create_snapshot(self):
        template= TemplateSnapshot(self)
                           
        dirname= os.path.dirname(self.file.path)
        filename= os.path.basename(self.file.path)
        path= os.path.join(dirname, filename+ EXT_JSON_XZ)
                    
        with lzma.open(path, "wt") as f:
            json.dump(template.to_json(), f)
    
    @classmethod
    def from_file(cls, file: File):
        """
        Devuelve una instancia de PiecesFile que conserva el valor de .path
        """
        cache_pieces = os.path.join(cls.config.worktable, file.md5sum)
        
        if os.path.exists(cache_pieces):
            with open(cache_pieces, 'r') as f:
                json_data = json.load(f)
            pieces = []
            for doc in json_data["pieces"]:
                doc["message"]= MessagePlus() if doc["message"] else None
                pieces.append(Piece(**doc))
            return PiecesFile(file=file, pieces=pieces)

        return PiecesFile(file=file, pieces=None)