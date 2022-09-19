from typing import Union
from pathlib import Path
import os
import yaml
import json

from .file import File
from ..telegram import Messageplus, Telegram, telegram
from ..constants import WORKTABLE, EXT_JSON, EXT_YAML,VERSION
from ..functions import check_file_name_length


class Singlefile:
    def __init__(self,
                 file: File,
                 message: Messageplus = None
                 ):
        self.file = file
        self.message = message
    
    def load(self):
        document = self.file._load()
        if document.get('message'):
            self.message= Messageplus(**document["message"]) if document["message"] else None
        return self
    def update(self,remove=False): 
        """
        remove: True para eliminar el archivo una vez se sube a telegram.
        Enviar el archivo a telegram y añade el resultado (message) a la propiedad self.message
        """       
        caption = self.filename
        filename = self.filename_for_telegram
        path= self.path
        self.message = telegram.update(path, caption=caption, filename=filename)
        if remove:
            os.remove(self.path)        
        
    def save(self, version=True):
        filename = self.file.inodo_name + EXT_JSON
        path = os.path.join(WORKTABLE, filename)
        document= self.__dict__.copy()
        if version:            
            document["version"]= VERSION 
        with open(path, "w") as file:
            json.dump(document,file, default= lambda x: x.to_json())
        return self

    def to_json(self):
        document = self.__dict__.copy()
        document["file"] = self.file.to_json()
        document["message"] = None if self.message == None else self.message.to_json()
        return document

    def to_fileyaml(self):
        filename = self.file.filename
        md5sum = self.file.md5sum
        size = self.file.size
        type = self.file.type
        version = self.file.version
        message = None if self.message is None else self.message.to_json()
        return {
            "filename": filename,
            "md5sum": md5sum,
            "size": size,
            "message": message,
            "type": type,
            "version": version
        }

    def create_fileyaml(self, path: Union[str, Path]):
        filename = os.path.basename(self.file.filename)
        dirname = os.path.dirname(self.file.path)
        ext = getattr(path, "suffix", None) or os.path.splitext(path)[1]

        name = filename.replace(ext, "") + EXT_YAML
        path = os.path.join(dirname, name)
        with open(path, "w") as file:
            yaml.dump(self.to_json(), file, sort_keys=False)
    @property
    def type(self):
        return self.file.type
    @property
    def filename(self):
        return self.file.filename        
    @property
    def path(self):
        return self.file.path
    @property
    def filename_for_telegram(self):
        if check_file_name_length(self.file.path):
            return self.file.filename
        return self.file.md5sum + self.file.suffix

    @property
    def is_finalized(self):
        """
        True si el archivo ha sido subido.
        """
        if self.message:
            return True
        return False