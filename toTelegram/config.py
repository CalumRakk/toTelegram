
import yaml
import os
from .functions import attributes_to_json,any_to_list, any_to_bytes
from .constants import PATH_CONFIG


class OptionalArguments:
    def __init__(self,**kwargs):
        self.exclude_words = any_to_list(kwargs.get("exclude_words")) 
        self.exclude_ext = any_to_list(kwargs.get("exclude_ext"))        
        self.min_size = any_to_bytes(kwargs.get("min_size"))   
        self.max_size = any_to_bytes(kwargs.get("max_size"))


class ExclusionManager:
    def __init__(self, args):
        self.args= OptionalArguments(**args.__dict__)
        self.config= OptionalArguments(**Config._load_file_config())

    def _exclusion_by_words(self, path):
        ext = os.path.splitext(path)[1]
        name = os.path.basename(path).replace(ext, "")
               
        for word in self.args.exclude_words:
            if word in name:
                return True        
        for word in self.config.exclude_words:
            if word in name:
                return True         
        return False

    def _exclusion_by_ext(self, path):
        ext = os.path.splitext(path)[1]       
        
        if ext in self.args.exclude_ext or ext in self.config.exclude_ext: 
            return True
        return False

    def _exclusion_by_min_size(self, path):     
        if type(self.args.min_size)==int:
            filesize = os.path.getsize(path)
            if filesize < self.args.min_size:
                return True
        elif type(self.config.min_size)==int:
            filesize = os.path.getsize(path)
            if filesize < self.config.min_size:
                return True
        return False

    def _exclusion_by_max_size(self, path):
        if type(self.args.max_size)==int:
            filesize = os.path.getsize(path)
            if filesize > self.args.max_size:
                return True
        
        elif type(self.config.max_size)==int:
            filesize = os.path.getsize(path)
            if filesize > self.config.max_size:
                return True
        return False

    def is_skipped(self, path):        
        if self._exclusion_by_ext(path):
            return True
        elif  self._exclusion_by_words(path):
            return True
        elif self._exclusion_by_min_size(path):
            return True
        elif self._exclusion_by_max_size(path):
            return True
        return False
    
    def __call__(self, paths:list):
        """
        Devuelve una lista de path sin los archivos que cumplan alguno de los argumentos de exclusión.
        Parametros:
            paths (list of strings):
                Una lista de rutas de los archivos a filtrar
        """        
        for path in paths[:]:
            if self.is_skipped(path): 
                paths.remove(path)
        return paths
        

class Config(OptionalArguments):    
    path = PATH_CONFIG
    
    def __init__(self):
        config = Config._load_file_config()

        self.api_hash = config["api_hash"]
        self.api_id = config["api_id"]
        self.chat_id = config["chat_id"]
        self.session_string = config.get("session_string", None)
        super().__init__(**config)

    def to_json(self):
        return attributes_to_json(self)
    
    @classmethod
    def _create_file_config(cls):
        json_data = {"API_HASH": "e0n7bf4d",
                     "API_ID": 1585711,
                     "CHAT_ID": "https://t.me/+Fz1aDRT"}
        with open(cls.path, "wt") as fb:
            yaml.dump(json_data, fb, sort_keys=False)
    
    @classmethod
    def _load_file_config(cls):
        if not os.path.exists(cls.path):
            cls._create_file_config()
            print("Para continuar es necesario rellenar los datos de config.yaml")
            exit()

        with open(cls.path, "rt") as fb:
            config = yaml.load(fb, Loader=yaml.UnsafeLoader)

        if config == None:
            raise Warning("El archivo config está vacio.")

        config_to_lower = {}
        for key, value in config.items():
            config_to_lower[key.lower()] = value
        return config_to_lower

    def _save_file_config(self):
        path= Config.path
        json_data = self.to_json()

        config_to_upper = {}
        for key, value in json_data.items():
            config_to_upper[key.upper()] = value

        with open(path, "wt") as fb:
            yaml.dump(config_to_upper, fb, sort_keys=False)

