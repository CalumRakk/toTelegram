
from .file import File
from .managers import (PiecesFile, SingleFile, FolderFile)


def update(path):
    file= File.from_path(path)
    
    if file.type=="pieces-file":
        manager= PiecesFile.from_file(file)
    else:
        manager= SingleFile(file=file, message=None)
    
    manager.update()
    manager.create_snapshot()
    