import os

import yaml

from .constants import PATH_CONFIG_FILE

class Config:
    def __init__(self) -> None:
        # TODO: atributos deben ser en minúsculas.
        config = self.load()
        if config is None:
            self.USERNAME = "me"
            self.API_ID = ""
            self.API_HASH = 0            
            self.CHAT_ID = -1
            self.save()
            exit()
        self.__dict__=config.__dict__
    
    def load(self):
        if not os.path.exists(PATH_CONFIG_FILE):
            return None
        with open(PATH_CONFIG_FILE, "r") as f:
            config= yaml.load(f, Loader=yaml.UnsafeLoader)
        
        fail=False
        if config.API_ID==0:
            fail=True
            print("No se ha configurado la API_ID")
        if config.API_HASH=="":
            fail=True
            print("No se ha configurado el API_HASH")
        if config.CHAT_ID==-1:
            fail=True
            print("No se ha configurado el CHAT_ID")
        if fail==True:
            exit()
        return config    
    
    def save(self):        
        with open(PATH_CONFIG_FILE, "w") as f:
            yaml.dump(self.__dict__, f)
        
        