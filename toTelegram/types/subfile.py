
import os
from .file import File
from ..functions import get_or_create_md5sum, create_mimeType, get_or_create_metadata
from pathlib import Path

class SubFile(File):       
    def __init__(self, folder, filename=None, fileExtension=None, mimeType=None, md5sum=None, size=None, metadata=None):
        self.folder = folder
        super().__init__(filename=filename, fileExtension=fileExtension, mimeType=mimeType,
                          md5sum=md5sum, size=size, metadata=metadata)
        
    @classmethod
    def from_json(cls, json_data):
        # TODO: POR CONVENCIÓN TODO MÉTODO from_json tiene que validar los tipos.
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
    
    
