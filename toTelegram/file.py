
import os
import json
from typing import Optional, Union
from .functions import get_or_create_md5sum, attributes_to_json, create_mimeType, get_or_create_metadata
from datetime import datetime
from .constants import FILESIZE_LIMIT, WORKTABLE
from pathlib import Path


class File:
    """ 
    Representa a un archivo.

    Parametros:
        id (``int``, *optional*):
            path es la ubicación del archivo. Si no se pasa el path se intenta buscar el archivo por medio del filename

        md5sum (Union[``str``,``bool``]=True, *optional*):
            True (Por defecto) se intenta cargar o generar el md5sum.\n
            False se establece el valor en None.\n
            str se establece al valor del string
    """
    @classmethod
    def from_json(cls, Json):
        return File(**Json)

    @classmethod
    def from_path(cls, path):
        path = str(path)
        filename = os.path.basename(path)
        fileExtension = os.path.splitext(path)[1]
        mimeType = create_mimeType(path)
        md5sum = get_or_create_md5sum(path)
        size = os.path.getsize(path)
        metadata = get_or_create_metadata(path, mimeType, md5sum)

        file = File(filename=filename,
                    fileExtension=fileExtension,
                    mimeType=mimeType,
                    md5sum=md5sum,
                    size=size,
                    metadata=metadata
                    )
        file._path = path
        return file

    def __init__(self, filename=None, fileExtension=None, mimeType=None, md5sum=None, size=None, metadata=None):
        self.kind = "file"
        self.filename = filename
        self.fileExtension = fileExtension
        self.mimeType = mimeType
        self.md5sum = md5sum
        self.size = size
        self.metadata = metadata

    def save(self):
        path = os.path.join(WORKTABLE, self.md5sum)
        with open(path, "w") as f:
            json.dump(self.to_json(), f)

    def to_json(self):
        return attributes_to_json(self)

    @property
    def path(self):
        return getattr(self, "_path", None)

    @property
    def type(self):
        """
        Devuelve el tipo de manager que se deberia usar con este archivo.
        """
        return "pieces-file" if self.size > FILESIZE_LIMIT else "single-file"


class SubFile(File):
    @classmethod
    def from_json(cls, json_data):
        return SubFile(**json_data)
    @classmethod
    def from_path(cls, path):        
        path = str(path)
        folder= list(Path(path).parent.parts[1:])
        filename = os.path.basename(path)
        fileExtension = os.path.splitext(path)[1]
        mimeType = create_mimeType(path)
        md5sum = get_or_create_md5sum(path)
        size = os.path.getsize(path)
        metadata = get_or_create_metadata(path, mimeType, md5sum)

        file = SubFile(folder=folder,
                    filename=filename,
                    fileExtension=fileExtension,
                    mimeType=mimeType,
                    md5sum=md5sum,
                    size=size,
                    metadata=metadata
                    )
        file._path = path
        return file
    
        
    def __init__(self, folder, filename=None, fileExtension=None, mimeType=None, md5sum=None, size=None, metadata=None):
        self.folder = folder
        super().__init__(filename=filename, fileExtension=fileExtension, mimeType=mimeType,
                          md5sum=md5sum, size=size, metadata=metadata)
    
