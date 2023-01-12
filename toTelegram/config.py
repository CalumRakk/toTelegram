
from typing import Union
import os
import yaml

from .constants import WORKTABLE, PATH_CONFIG



def load_config()->dict:
    """
    Devuelve las variables del archivo config. Cada key se devuelve en miniscula.
    """
    with open(PATH_CONFIG, "rt", encoding="utf-8") as fb:
        config = yaml.load(fb, Loader=yaml.UnsafeLoader)

    if config is None:
        raise Warning("El archivo config está vacio.")

    config_to_lower = {}
    for key, value in config.items():
        config_to_lower[key.lower().replace("-","_")] = value
    return config_to_lower


def check_file_config():
    if not os.path.exists(PATH_CONFIG):
        # create_file_config(PATH_CONFIG)
        print("Para continuar es necesario rellenar los datos de config.yaml")
        exit()


class OptionalExclusionArguments:
    def __init__(self,
                 exclude_words: list,
                 exclude_ext: list,
                 min_size: Union[None, int],
                 max_size: Union[None, int]):
        self.exclude_words = exclude_words
        self.exclude_ext = exclude_ext
        self.min_size = min_size
        self.max_size = max_size

    @classmethod
    def from_json(cls, d: dict):
        return OptionalExclusionArguments(exclude_words=d["exclude_words"],
                                          exclude_ext=d["exclude_ext"],
                                          min_size=d["min_size"],
                                          max_size=["max_size"])


class Config(OptionalExclusionArguments):
    check_file_config()
    yaml_data = load_config()
    # VARIABLES PREDETERMINADAS
    api_hash = yaml_data["api_hash"]
    api_id = yaml_data["api_id"]
    chat_id = yaml_data["chat_id"]
    session_string = yaml_data.get("session_string")

    # VARIABLES NO REPETIRSE
    worktable = yaml_data.get("worktable") or WORKTABLE
    path_snapshot_files = yaml_data.get("path_snapshot_files")

    exclude_words = yaml_data.get("exclude_words")
    exclude_ext = yaml_data.get("exclude_ext")
    min_size = yaml_data.get("min_size")
    max_size = yaml_data.get("max_size")

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
    def save_config(cls, config: dict, sort_keys=False):
        config_to_upper = {}

        for key, value in config.items():
            config_to_upper[key.upper()] = value

        with open(PATH_CONFIG, "wt", encoding="utf-8") as fb:
            yaml.dump(config_to_upper, fb, sort_keys=sort_keys)


class Folder:
    def __init__(self, worktable):
        self._worktable= worktable

        self._md5sum_folder_name= "md5sums"
        self._chunk_folder_name= "chunks"
        self._metadata_folder_name= "metadata"
        self._backup_folder_name= "backups"
    
    @property
    def worktable(self):
        if not isinstance(self._worktable, str):
            if not os.path.exists(WORKTABLE):
                os.makedirs(WORKTABLE)
            self._worktable= WORKTABLE
        return self._worktable

    @property
    def path_md5sum(self):
        if hasattr(self, "_path_md5sum") is False:
            path_md5sum = os.path.join(self._worktable, self._md5sum_folder_name)
            if not os.path.exists(path_md5sum):
                os.makedirs(path_md5sum)
            setattr(self, "_path_md5sum", path_md5sum)
        return getattr(self, "_path_md5sum")
    
    @property
    def path_chunk(self):
        if hasattr(self, "_path_chunk") is False:
            path = os.path.join(self._worktable, self._chunk_folder_name)
            if not os.path.exists(path):
                os.makedirs(path)
            setattr(self, "_path_chunk", path)
        return getattr(self, "_path_chunk")
    @property
    def path_metadata(self):
        if hasattr(self, "_path_metadata") is False:
            path = os.path.join(self._worktable, self._metadata_folder_name)
            if not os.path.exists(path):
                os.makedirs(path)
            setattr(self, "_path_metadata", path)
        return getattr(self, "_path_metadata")
   
    @property
    def path_backups(self):
        if hasattr(self, "_path_backups") is False:
            path = os.path.join(self._worktable, self._backup_folder_name)
            if not os.path.exists(path):
                os.makedirs(path)
            setattr(self, "_path_backups", path)
        return getattr(self, "_path_backups")

class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):

        if cls not in cls._instances:
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]

attributes= ["exclude_words", "exclude_ext", "min_size", "max_size"]
class Config2(Folder, metaclass=SingletonMeta):
    def __init__(self):
        json_data= load_config()
        self.api_hash = json_data["api_hash"]
        self.api_id = json_data["api_id"]
        self.chat_id = json_data["chat_id"]
        self._session_string =  json_data.get("session_string")
        super().__init__(worktable=json_data.get("worktable"))
        
        # Si uno de los campos de json_data está en la lista de attributes, el campo se creará como un atributo de este objeto.
        for key, value in json_data.items():
            if key in attributes:
                setattr(self, key, value)
    
    def __getattr__(self, value):
        """Devuelve None cuando el atributo está dentro de la lista 'attributes' sino está devuelve un raise.
        """
        # Python llama a este método cuando se accede a un atributo del objeto que no existe.
        if value in attributes:
            return None
        raise AttributeError("El atributo no existe")
    
    @classmethod
    def insert_or_update_field(cls, document: dict):
        config = load_config()
        config.update(document)
        cls.save_config(config)

    @classmethod
    def save_config(cls, config: dict, sort_keys=False):
        config_to_upper = {}

        for key, value in config.items():
            config_to_upper[key.upper()] = value

        with open(PATH_CONFIG, "wt", encoding="utf-8") as fb:
            yaml.dump(config_to_upper, fb, sort_keys=sort_keys)
