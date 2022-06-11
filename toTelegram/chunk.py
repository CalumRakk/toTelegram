from __future__ import annotations
import os
from typing import TYPE_CHECKING

from .functions import get_part_filepart
if TYPE_CHECKING:
    from .file import File
from .messageplus import Messageplus


class Chunk:
    def __init__(self,path, parent:File):
        self.path= path
        self.filepart:str= os.path.basename(path)
        self.parent= parent

        self.part= get_part_filepart(self.filepart) 
        self.filename=  self.filepart.replace("_"+self.part,"")
        self.message= None
    
    @property
    def is_online(self):
        if self.message is None:
            return False
        return True
    def update(self):
        message= self.parent.telegram.update(self.path,caption=False)
        message= Messageplus(message)
        self.message= message
        self.parent.messages.append(message)
        self.parent.save()
        
    
