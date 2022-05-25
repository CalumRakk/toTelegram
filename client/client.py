
import os

from .logging_ import Logging
from .constants import EXT_GZ
from .functions import split_file, compress_file


class Client(Logging):
    def __init__(self, path, md5sum,**kwargs):
        # self= Logging(self.path, md5sum)
        super().__init__(**kwargs)
         
    def split(self,**kwargs):
        path= self.path
        folder= self.folder
        
        if len(self.splitters)<1:           
            splitters= split_file(input_=path, output_folder=folder, **kwargs)
            self.splitters= splitters
        
        for file in self.splitters:
            if not self.is_compressed(file):
                gzfile= compress_file(file,output_folder=folder)
                self.add_to_compressed_files(gzfile)
                os.remove(file)
        
        