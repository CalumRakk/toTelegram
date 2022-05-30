from typing import List
import os

from .logging_ import Logging
from .constants import FILESIZE
from .functions import split_file, compress_file, load_config
from .telegram import Telegram

class Client(Logging):
    def __init__(self, path, md5sum,**kwargs):
        # self= Logging(self.path, md5sum)
        self.kwargs= kwargs
        super().__init__(path,md5sum)
              
    def split(self,size=FILESIZE,verbose=True,quality=-1,**kwargs)-> List[str]:
        """
        Divide el archivo en trozos de tamaño size
        """
        if not self.is_complete_split:
            files= split_file(self.path, self.folder, size=size, verbose=verbose)            
            self.split_files= files
            self.is_complete_split= True
        return self.is_complete_split
            
    def compress(self,verbose=True,quality=-1,**kwargs)-> List[str]:
        """
        Comprime los archivos divididos segun Logging.
        """
        if not self.is_complete_compressed:
            for file in self.split_files:
                if file not in self.compressed_files:
                    path= os.path.join(self.folder, file)                    
                    self.compressed_files= compress_file(path, self.folder, verbose=verbose,quality=quality)
            self.is_complete_compressed= True
        self.compressed_files
            
    def update(self,**kwargs)-> List[str]:
        """
        Sube los archivos comprimidos segun Logging a Telegram archivos.
        """
        config= load_config()
        telegram= Telegram(config)
        
        self.split(**kwargs)
        if not self.is_complete_uploaded:
            for file in self.compressed_files:
                if not self.is_file_uploaded(file):
                    path= os.path.join(self.folder, file)
                    response= telegram.upload_file(path)
                    self.uploaded_files= response            
            self.is_complete_uploaded= True
        return self.uploaded_files
    