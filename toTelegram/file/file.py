import os
from json.decoder import JSONDecodeError
import json
from pathlib import Path
from typing import Union,Optional

from ..constants import VERSION, FILESIZE_LIMIT, WORKTABLE, EXT_JSON,FILE_NOT_FOUND,BACKUP
from ..functions import get_md5sum_by_hashlib


class File:
    """Un Archivo.

    Parameters:
        id (``int``, *optional*):
            path es la ubicación del archivo. Si no se pasa el path se intenta buscar el archivo por medio del filename

        md5sum (Union[``str``,``bool``]=True, *optional*):
            True (Por defecto) se intenta cargar o generar el md5sum.\n
            False se establece el valor en None.\n
            str se establece al valor del string
    """
    def __init__(self, 
            path: Optional[Union[Path, str]]=None, # 
            filename:Optional[str]=None,
            # suffix: Optional[str]=None,
            size: Optional[int]=None,
            md5sum: Optional[Union[str,bool]]=True, # Por defecto los files no generan el md5sum a menos que se establezca en True
            # version: Optional[str]=None, 
        ):
        self._path = path if type(path) == str else str(path)         
        self.filename = filename or os.path.basename(self.path)
        # self.suffix= suffix or self.Path.suffix
        self.size = size or self.stat_result.st_size
        self.md5sum = self.get_md5sum(md5sum)
        # self.version = version or VERSION
    
    @property
    def suffix(self):
        return self.Path.suffix        
    @property
    def Path(self):
        if self.exists:            
            if not getattr(self, "_Path",False):
                self._Path= Path(self.path)
            return self._Path
        return FILE_NOT_FOUND
    
    @property
    def exists(self):
        if not getattr(self, '_exists',False):
            self._exists= os.path.exists(self._path)
            if self._exists==False:
                for folder in [WORKTABLE, BACKUP]:
                    build_path= os.path.join(folder, self.filename)
                    if os.path.exists(build_path):
                        self._path= build_path
                        self._exists=True
        return self._exists
    @property
    def type(self):
        return "pieces-file" if self.size > FILESIZE_LIMIT else "single-file"
    @property
    def path(self):
        if self.exists:
            return self._path
        return FILE_NOT_FOUND
    @property
    def stat_result(self):
        if self.exists:
            if not getattr(self, "_stat_result", False):
                self._stat_result= os.stat(self.path)
            return self._stat_result            
        return FILE_NOT_FOUND
        
    @property
    def inodo_name(self):
        return str(self.stat_result.st_dev) + "-" + str(self.stat_result.st_ino)  
    
    def get_md5sum(self, md5sum):
        if type(md5sum)==str:
            return md5sum
        if md5sum==True:
            if self.exists:
                json_data= self._load()
                if json_data:
                    return json_data["file"].get('md5sum')
            return get_md5sum_by_hashlib(self.path)   
        return None        
                        
    def is_valid(self):
        pass
    
    def save(self):
        filename = self.inodo_name + EXT_JSON
        path= os.path.join(WORKTABLE, filename)
        json_data = self.to_json()

        with open(path, "w") as file:
            json.dump(json_data, file)
            
    def _load(self):
        filename = self.inodo_name + EXT_JSON
        path= os.path.join(WORKTABLE, filename)        
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except JSONDecodeError:
                pass
        return None

    def to_json(self):
        """
        Devuelve un json con las propiedades del objeto, excepto las propiedades privadas. 
        """
        document= self.__dict__.copy()
        for key in self.__dict__.keys():
            if key.startswith("_"):
                document.pop(key)
        return document
