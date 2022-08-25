from typing import Union
from pathlib import Path
import os
import yaml

from .file import File
from ..telegram import Messageplus, Telegram, telegram
from ..constants import EXT_YAML
from ..functions import check_file_name_length

class Singlefile:
    def __init__(self, file: File):
        self.file= file
        self.type = "single-file"     
        self.message= self._load_message(telegram)
    @property
    def filename(self):
        if check_file_name_length(self.file.path):        
            return self.file.filename 
        return self.file.md5sum + self.file.suffix
    @property
    def is_finalized(self): 
        """
        True si todas las piezas han sido subido.
        False si el atributo pieces es una lista vacia o alguna pieza no ha sido subido.
        """
        if self.message:      
            return True      
        return False   
    def _load_message(self, telegram: Telegram):
        json_data = self.file._load_file()
        message=None        
        if json_data:
            json_message: dict = json_data["message"]
            if json_message:
                link = json_message["link"]
                message = Messageplus(telegram.get_message(link))                
        return message
    def to_fileyaml(self):
        pass
    def save(self):
        pass
    def to_json(self):
        filedocument= self.file.to_json()
        messagedocument= self.message.to_json()
        return {
            "file": filedocument,
            "type": self.type,
            "message": messagedocument    
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