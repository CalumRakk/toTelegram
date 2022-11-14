
import os
import yaml
# from .functions import attributes_to_json,any_to_list, any_to_bytes
from .constants import WORKTABLE, PATH_CONFIG


def load_config():
    """
    Devuelve las variables del archivo config. Cada key se devuelve en miniscula.
    """
    with open(PATH_CONFIG, "rt") as fb:
        config = yaml.load(fb, Loader=yaml.UnsafeLoader)

    if config == None:
        raise Warning("El archivo config está vacio.")

    config_to_lower = {}
    for key, value in config.items():
        config_to_lower[key.lower()] = value
    return config_to_lower

def check_file_config():
    if not os.path.exists(PATH_CONFIG):
        # create_file_config(PATH_CONFIG)
        print("Para continuar es necesario rellenar los datos de config.yaml")
        exit()
    

class Config:
    check_file_config()        
    config = load_config()
    # TELEGRAM
    api_hash = config["api_hash"]
    api_id = config["api_id"]
    chat_id = config["chat_id"]
    session_string = config.get("session_string", None)

    # VARIABLES NO REPETIRSE
    worktable = config.get("worktable") or WORKTABLE
    registry = config.get("registry")
    
    
    # Estos path se generán dentro de worktable. No tocar.
    path_md5sum = os.path.join(WORKTABLE, "md5sums")
    path_chunk = os.path.join(WORKTABLE, "chunks")
    path_metadata = os.path.join(WORKTABLE, "metadata")
    path_backups = os.path.join(WORKTABLE, "backups")

    if not os.path.exists(worktable):
        os.makedirs(worktable)
    if not os.path.exists(path_md5sum):
        os.makedirs(path_md5sum)
    if not os.path.exists(path_chunk):
        os.makedirs(path_chunk)
    if not os.path.exists(path_metadata):
        os.makedirs(path_metadata)
    if not os.path.exists(path_backups):
        os.makedirs(path_backups)

    @classmethod
    def insert_or_update_field(cls, document: dict):
        config = load_config()
        config.update(document)
        cls.save_config(config)
    
    
    @classmethod
    def save_config(cls, config:dict, sort_keys=False):
        config_to_upper = {}
        
        for key, value in config.items():
            config_to_upper[key.upper()] = value

        with open(PATH_CONFIG, "wt") as fb:
            yaml.dump(config_to_upper, fb, sort_keys=sort_keys)