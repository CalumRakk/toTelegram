import json
import os
from pathlib import Path
from typing import Union

import yaml

from ..constants import EXT_JSON, EXT_YAML, VERSION, WORKTABLE
from ..functions import check_file_name_length, attributes_to_json
from ..telegram import MessagePlus, telegram
from ..file import File


class SingleFile:
    def __init__(self,
                 file: File,
                 message= None
                 ):
        self.kind = "single-file"
        self.file = file
        self.message = message
        
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

    def to_json(self):
        return attributes_to_json(self)

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