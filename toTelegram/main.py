
from .folder.folder import Folder
import os
from . import File, Piecesfile, Singlefile
import shutil
from pathlib import Path
def concatenate(args):
    path=args.path
    path_snapshot= args.snapshot or os.path.join(os.path.dirname(path), os.path.basename(path) + ".snar")  
   
    folder= Folder(path,path_snapshot)
    if folder.is_new:
        files= folder.get_files()            
        folder.create_backup(files)
        folder.save()
        # if get_bytes(files) > x:      
        #     folder.create_backup(files)
        #     folder.save()
            
    for backup in folder.backups:
        if backup.file.type=="pieces-file":        
            if not backup.file.is_split_finalized:
                backup.file.split()
                folder.save()
                
            for piece in backup.file.pieces:
                if not piece.message:
                    piece.update(remove=True)
                    folder.save()
        else:  
            if not backup.file.message:
                backup.update(remove=True)
                folder.save()
        if os.path.exists(backup.file.path):
            os.remove(backup.file.path)
    
    if os.path.exists(path):
        source=r"D:\Usuarios\Leo\Escritorio\github Leo\toTelegram\toTelegram\assets\ok.ico" 
        out=os.path.join(path,"Desktop.ini")
        shutil.copyfile(source,out)  
        os.system(f'attrib +s "{path}"')

def update(path):
    path= Path(path)
        
    paths= path.glob("*.*") if path.is_dir() else [path]
        
    for path in paths:        
        file = File(path) # File(path).load()
        if file.type == "pieces-file":        
            piecesfile = Piecesfile(file).load()
            if not piecesfile.is_finalized:
                if not piecesfile.is_split_finalized:
                    piecesfile.split()
                    piecesfile.save()
                
                for piece in piecesfile.pieces:
                    if piece.message==None:
                        piece.update()
                        os.remove(piece.file.path)
                        piecesfile.save()
                    
            piecesfile.create_fileyaml(path)
        else:
            singlefile = Singlefile(file).load().save()
            if not singlefile.is_finalized:
                singlefile.update()
                singlefile.save()
            singlefile.create_fileyaml(path)
     

      