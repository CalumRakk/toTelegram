
import os
from .functions import get_all_files_from_directory
from .file import File
from .managers import (PiecesFile, SingleFile, FolderFile)
from .config import ExclusionManager

def check_point(args):
    """
    Devuelve una lista de paths filtrada.
    """
    path= args.path
    if os.path.isdir(path):
        paths = get_all_files_from_directory(path)
        paths= ExclusionManager(args)(paths)             
    elif os.path.isfile(path): # Si path es un archivo no se filtra nada.
        return [path]
    else:
        raise FileNotFoundError


def update(args):
    paths= check_point(args)
    count_path= len(paths) 
       
    for index, path in enumerate(paths, 1):                
        print(f"\n{index}/{count_path}", os.path.basename(path))        
        file = File.from_path(path)

        if file.type == "pieces-file":
            manager = PiecesFile.from_file(file)
        else:
            manager = SingleFile(file=file, message=None)

        manager.update()
        manager.create_snapshot()
