
import json
import os
import shutil
import tarfile
from json.decoder import JSONDecodeError
from typing import List, Optional

from ..constants import EXT_TAR, VERSION, WORKTABLE
from ..file.file import File
from ..file.piece import Piece
from ..file.piecesfile import Piecesfile
from ..file.singlefile import Singlefile
from ..telegram import telegram
from ..telegram.messageplus import Messageplus
from .backup import Backup
from .multifile import Multifile


class Audio:
    pass

class Image:
    def __init__(self, width, height):
        self.width = width
        self.height = height


class Video(Image):
    def __init__(self, bitrate,fps, width, height, durationMillis):
        self.durationMillis = durationMillis
        self.bitrate = bitrate
        self.fps= fps
        super().__init__(width, height)


class Folder:
    def __init__(self,
                 path: str,
                 snapshot: str,
                 backups: Optional[List[Backup]] = [],
                 version: Optional[str] = VERSION
                 ):
        self.folder = str(path)
        self.snapshot = str(snapshot)

        self.backups = self._get_backups()
        self.version = version

    def _get_backups(self):
        backups = []
        json_data = self._load().get("backups")
        if json_data:
            for backup_document in json_data:
                file_document: dict = backup_document["file"]["file"]
                files_document: list = backup_document["files"]
                files = [Multifile(**document)
                         for document in files_document]

                file = File(**file_document)
                if file.type == "pieces-file":
                    pieces = []
                    for piece_document in backup_document["file"]["pieces"]:
                        filename = piece_document["filename"]
                        path = os.path.join(WORKTABLE, filename)
                        size = piece_document["size"]
                        md5sum = file.md5sum
                        message = Messageplus(
                            **piece_document["message"]) if piece_document["message"] else None
                        piece = Piece(path=path, filename=filename,
                                      size=size, message=message, md5sum=md5sum)
                        pieces.append(piece)

                    piecesfile = Piecesfile(file, pieces)
                    backup = Backup(piecesfile, files)
                else:
                    message = backup_document["file"]["message"]
                    messageplus = None if message == None else Messageplus(
                        **message)
                    file_ = backup_document["file"]["file"]
                    singlefile = Singlefile(File(**file_), messageplus)
                    backup = Backup(singlefile, files)
                backups.append(backup)
        return backups

    @ property
    def path(self):
        return self.folder

    @ path.setter
    def path(self, value):
        self.folder = value

    @ property
    def is_new(self):
        """
        True si el folder es nuevo. Se cumple como True cuando tiene elementos en self.brackups
        """
        if bool(self.backups) == True:
            return False
        return True

    def get_files(self):
        res = []
        for (dir_path, dir_names, file_names) in os.walk(self.path):
            res.extend([os.path.join(dir_path, filename)
                       for filename in file_names])
        files = []
        count_files = len(files)
        print("Generando md5sum de files")
        for index, path in enumerate(res):
            index += 1
            print(f"{index}/{count_files}", end='\r')
            file = Multifile(path)
            files.append(file)
        print("Done.")
        return files

    def to_json(self):
        document = self.__dict__.copy()
        document["backups"] = [backup.to_json() for backup in self.backups]
        document["folder"] = os.path.basename(self.folder)
        document["snapshot"] = os.path.basename(self.snapshot)
        for key in self.__dict__.keys():
            if key.startswith("_"):
                document.pop(key)
        return document

    @ property
    def filename_for_file_backup(self):
        name = os.path.basename(self.path)
        index = str(len(self.backups))
        filename = name + "_" + index + EXT_TAR
        return filename

    def save(self):
        json_data = self.to_json()

        with open(self.snapshot, "w") as file:
            json.dump(json_data, file)

    def create_backup(self, files):
        filename = self.filename_for_file_backup
        folder = os.path.join(WORKTABLE, "backups")

        if not os.path.exists(folder):
            os.makedirs(folder)

        path = os.path.join(folder, filename)
        with tarfile.open(path, "w") as tar:
            for file in files:
                tar.add(file.path)
        file = File(path)
        file = Piecesfile(
            file, pieces=[]) if file.type == "pieces-file" else Singlefile(file, message=None)
        backup = Backup(file, files)
        self.backups.append(backup)

        return backup

    def _load(self):
        if os.path.exists(self.snapshot):
            try:
                with open(self.snapshot, "r") as f:
                    return json.load(f)
            except JSONDecodeError:
                pass
        return {}

    def update(self):
        if self.is_new:
            files = self.get_files()
            self.create_backup(files)
            self.save()
        for backup in self.backups:
            file = backup.file
            if file.type == "pieces-file":
                piecefile: Piecesfile = file
                if not piecefile.is_split_finalized:
                    piecefile.pieces = piecefile.split()
                    self.save()

                for piece in piecefile.pieces:
                    if piece.message == None:
                        caption = piece.filename
                        filename = piece.filename_for_telegram
                        piece.message = telegram.update(
                            piece.path, caption=caption, filename=filename)
                        self.save()
                        os.remove(piece.path)
            else:
                singlefile: Singlefile = file
                if singlefile.message == None:
                    caption = singlefile.filename
                    filename = singlefile.filename_for_telegram
                    path = singlefile.path
                    singlefile.message = telegram.update(
                        path, caption=caption, filename=filename)
                    self.save()
                    os.remove(singlefile.file.path)
            if os.path.exists(backup.file.path):
                os.remove(backup.file.path)

            if os.path.exists(self.path):
                source = r"D:\Usuarios\Leo\Escritorio\github Leo\toTelegram\toTelegram\assets\Desktop.txt"
                out = os.path.join(self.path, "Desktop.ini")
                with open(source, "r") as f:
                    with open(out, "w") as file:
                        file.write(f.read())
                os.system(f'attrib +s "{self.path}"')


class MediaUpdate:
    def __init__(self,path):
        self.path= path
        self.file = File.fromPath(path)
    