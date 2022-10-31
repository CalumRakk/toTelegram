
import os
from .functions import get_all_files_from_directory
from .file import File
from .managers import (PiecesFile, SingleFile, FolderFile)
from .config import ExclusionManager



def update(args):
    if os.path.isdir(args.path):
        paths = get_all_files_from_directory(args.path)
        exclusion= ExclusionManager(args)        
        for path in paths[:]:
            if exclusion.is_skipped(path): 
                paths.remove(path)            
    elif os.path.isfile(args.path):
        paths = [args.path]
    else:
        raise FileNotFoundError

    count_path= len(paths)
    for index, path in enumerate(paths, 1):
        if path.endswith(".json.xz"): continue # TODO: remove
        
        print(f"\n{index}/{count_path}", os.path.basename(path))
        file = File.from_path(path)

        if file.type == "pieces-file":
            manager = PiecesFile.from_file(file)
        else:
            manager = SingleFile(file=file, message=None)

        manager.update()
        manager.create_snapshot()
