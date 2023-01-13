
import os
from pathlib import Path

from .file import File, get_or_create_md5sum,get_or_create_metadata
# TODO: las funciones importadas de file deberian estar en un mejor lugar.
from ..utils import create_mimeType



class SubFile(File):
    def __init__(self, folder,
                 filename=None,
                 fileExtension=None,
                 mimeType=None,
                 md5sum=None,
                 size=None,
                 metadata=None):
        self.folder = folder
        super().__init__(filename=filename, fileExtension=fileExtension, mimeType=mimeType,
                         md5sum=md5sum, size=size, metadata=metadata)

    @classmethod
    def from_json(cls, json_data): # pylint: disable=W0237
        return SubFile(**json_data)

    @classmethod
    def from_path(cls, path):
        path = str(path)
        folder = list(Path(path).parent.parts[1:])
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
        setattr(file,"_path", path)
        return file
