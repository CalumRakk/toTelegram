import os
from typing import Union,NewType
from .constants import PATH_CONFIG_FILE
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

    def __load(self,path):
        with open("setting\config.yaml","r") as file:
            data = yaml.load(file, Loader=yaml.UnsafeLoader)     
            return {key.lower():value for key, value in data.items()}       