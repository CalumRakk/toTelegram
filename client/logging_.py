
import os

import yaml

from .constants import WORKTABLE, LOGGING
from .functions import get_md5sum


class Logging:
    def __init__(self, path, md5sum,**kwargs) -> None:
        self.path = os.path.abspath(path)
        self.filename = os.path.basename(path)
        self.name = os.path.splitext(self.filename)[0]
        self.WORKTABLE = os.path.join(os.path.dirname(self.path), WORKTABLE)        

        # md5sum= kwargs.get("md5sum",None)
        self.md5sum = get_md5sum(self.path) if md5sum==None else md5sum
        self.folder = os.path.join(self.WORKTABLE, self.md5sum)   
        self.path_logging= os.path.join(self.folder, LOGGING)   
        
        self._document=  self._load_document()   

    def _load_document(self):
        if os.path.exists(self.path_logging):
            with open(self.path_logging, 'r') as file:
                return yaml.safe_load(file)
        else:
            return {}

    def _save_document(self):
        with open(self.path_logging, 'w') as file:
            yaml.dump(self._document, file)

    @property
    def splitters(self):
        data = self._document.get("splitters")
        if type(data) != list:
            return []
        return [fr"{os.path.join(self.folder, i)}" for i in data]
    @property
    def all_videos_uploaded(self):
        """
        Devuelve True si todos los videos han sido subidos
        """
        data= self._document.get("all_videos_uploaded")
        if type(data) != list:
            return False
        return len(data)==self.total_videos
    
    @splitters.setter
    def splitters(self, value: list):
        self._document.update({"splitters": value})
        self._save_document()
        
    def is_compressed(self, value):
        data= self._document.get("compressed")
        if type(data) != list:
            return False
        return value in data
    
    def is_uploaded(self,value):
        data= self._document.get("uploaded")
        if type(data) != list:
            return False
        return value in data
    def add_to_compressed_files(self, file):
        """
        Añade un archivo a la lista de archivos comprimidos
        """
        filesLy= self._document.get("compressed")
        if type(filesLy) != list:
            filesLy= []
        
        filesLy.append(file)
        self._document.update({"compressed": filesLy})
        self._save_document()
