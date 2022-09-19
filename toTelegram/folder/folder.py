import os
import tarfile
import json
from json.decoder import JSONDecodeError
from typing import List, Optional

from ..file.file import File
from ..file.piecesfile import Piecesfile
from ..file.singlefile import Singlefile
from ..telegram.messageplus import Messageplus

from .backup import Backup
from ..constants import EXT_TAR, WORKTABLE, EXT_JSON, VERSION
from ..functions import check_file_name_length
from .multifile import Multifile


class Folder:
    def __init__(self,
                 path: str,
                 snapshot: str,
                 backups: Optional[List[Backup]] = [],
                 version: Optional[str] = VERSION
                 ):
        self.folder = str(path)
        self.snapshot = str(snapshot)
        self._json_date = self._load()
        self.backups = self._get_backups() or backups
        self.version = version

    def _get_backups(self):
        backups = []
        if self._json_date.get("backups"):
            for backup_document in self._json_date["backups"]:
                file_document: dict = backup_document["file"]["file"]
                files_document: list = backup_document["files"]
                files = [Multifile(**document)
                                       for document in files_document]
                file= File(**file_document)                
                
                if file.type == "pieces-file":                 
                    pieces= []   
                    for document in backup_document["file"]["pieces"]:
                        message= document["message"]
                        file_= document["file"]
                        messageplus= None if message == None else Messageplus(**message)
                        piece=Singlefile(File(**file_), messageplus)
                        pieces.append(piece)
                                       
                    piecesfile = Piecesfile(file,pieces)                  

                    backup = Backup(piecesfile,files)
                    backups.append(backup)
                    continue                
                message= backup_document["file"]["message"]
                messageplus= None if message == None else Messageplus(**message)
                file_= backup_document["file"]["file"]
                singlefile=Singlefile(File(**file_), messageplus)
                backup = Backup(singlefile,files)
                backups.append(backup)
        return backups

    @ property
    def path(self):
        return self.folder
    @ path.setter
    def path(self, value):
        self.folder=value

    @ property
    def is_new(self):
        """
        True si el folder es nuevo. Se cumple como True cuando tiene elementos en self.brackups
        """
        if bool(self.backups) == True:
            return False
        return True

    def get_files(self):
        res=[]
        for (dir_path, dir_names, file_names) in os.walk(self.path):
            res.extend([os.path.join(dir_path, filename)
                       for filename in file_names])
        files=[]
        for path in res:
            file=Multifile(path)
            files.append(file)
        return files

    def to_json(self):
        document=self.__dict__.copy()
        document["backups"]=[backup.to_json() for backup in self.backups]
        document["folder"]=os.path.basename(self.folder)
        document["snapshot"]=os.path.basename(self.snapshot)
        for key in self.__dict__.keys():
            if key.startswith("_"):
                document.pop(key)
        return document

    @ property
    def filename_for_file_backup(self):
        name=os.path.basename(self.path)
        index=str(len(self.backups))
        filename=name + "_" + index + EXT_TAR
        return filename

    def save(self):
        json_data=self.to_json()

        with open(self.snapshot, "w") as file:
            json.dump(json_data, file)

    def create_backup(self, files):
        filename=self.filename_for_file_backup
        folder=os.path.join(WORKTABLE, "backups")

        if not os.path.exists(folder):
            os.makedirs(folder)

        path=os.path.join(folder, filename)
        with tarfile.open(path, "w") as tar:
            for file in files:
                tar.add(file.path)
        file=File(path)
        file=Piecesfile(
            file) if file.type == "pieces-file" else Singlefile(file)
        backup=Backup(file, files)
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
