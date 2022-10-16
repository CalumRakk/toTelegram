
import os
from pathlib import Path

from . import File, Piecesfile, Singlefile
from .folder.folder import Folder


def concatenate(args):
    path=args.path
    path_snapshot= args.snapshot or os.path.join(os.path.dirname(path), os.path.basename(path) + ".snar")  
    folder= Folder(path,path_snapshot)
    folder.update()

filter= lambda x : x.suffix in [".yaml", ".jpg"]

def get_files(path):
    """
    Método para update
    """
    path= Path(path)
    if path.is_file():
        if filter(path):
            print(path,"\n","El archivo a subir está dentro de los archivos no permitodos.")
            exit()
        return [path] 
    paths=[]
    for path in path.glob("*.*"):
        if path.is_dir() or filter(path):
            continue
        paths.append(path)
    return paths
   

def update(path):   
    paths= get_files(path)
    count_paths= len(paths)
        
    for index, path in enumerate(paths):
        index+=1  
        print(f"{index}/{count_paths}", path)      
        file = File(path) # File(path).load()
        if file.type == "pieces-file":        
            piecesfile = Piecesfile(file,pieces=[])  # FIXME:  error #1  toca establecer el argumento en vacio para evitar que se conserve el valor 
            if not piecesfile.is_finalized:
                piecesfile.update()                                   
                piecesfile.create_fileyaml(path)
            continue
        singlefile= Singlefile(file, message=None) 
        if not singlefile.is_finalized:
            singlefile.update()
            singlefile.create_fileyaml(path)
            