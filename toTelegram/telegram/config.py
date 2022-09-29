import os
from typing import Union,NewType
from ..constants import PATH_CONFIG_FILE
from pathlib import Path
import yaml


class Config:
    """Pyrogram Client, the main means for interacting with Telegram.

    Parameters:
        target (``str`` | ``dict``):
            El json para construir el objeto. 
            str ubicación del archivo json.
            Pue

    """
    def __init__(self, target:Union[str,dict]) -> None:
        config = target if type(target) == dict else self.__load(target)
        self.__dict__.update(config)
        # self.name= config["username"]
        # self.api_id= config["api_id"]
        # self.api_hash= config["api_hash"]
        # self.chat_id= config["chat_id"]
    def __load(self,path):
        with open("setting\config.yaml","r") as file:
            data = yaml.load(file, Loader=yaml.UnsafeLoader)     
            return {key.lower():value for key, value in data.items()}       
    # def load(self):
    #     if not os.path.exists(PATH_CONFIG_FILE):
    #         return None
    #     with open(PATH_CONFIG_FILE, "r") as f:
    #         config= yaml.load(f, Loader=yaml.UnsafeLoader)
        
    #     fail=False
    #     if config.API_ID==0:
    #         fail=True
    #         print("No se ha configurado la API_ID")
    #     if config.API_HASH=="":
    #         fail=True
    #         print("No se ha configurado el API_HASH")
    #     if config.CHAT_ID==-1:
    #         fail=True
    #         print("No se ha configurado el CHAT_ID")
    #     if fail==True:
    #         exit()
    #     return config    
    
    # def save(self):        
    #     with open(PATH_CONFIG_FILE, "w") as f:
    #         yaml.dump(self.__dict__, f)
        
        