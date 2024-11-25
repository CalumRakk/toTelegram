import os
import yaml
from pathlib import Path

from .constants import WORKTABLE
from .utils import SingletonMeta


class Folder:
    def __init__(self, worktable):
        self._worktable = worktable

        self._md5sum_folder_name = "md5sums"
        self._chunk_folder_name = "chunks"
        self._metadata_folder_name = "metadata"
        self._backup_folder_name = "backups"

    @property
    def worktable(self):
        if not isinstance(self._worktable, str):
            if not os.path.exists(WORKTABLE):
                os.makedirs(WORKTABLE)
            self._worktable = WORKTABLE
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


class Config(Folder, metaclass=SingletonMeta):
    """Representa al archivo de `config.yaml`

    Args:
        Folder: hereda de folder las propiedas que devuelven las rutas
        metaclass (_type_, optional): _description_. Defaults to SingletonMeta.

    Nota: Las key en `config.yaml` no distinguen entre mayusculas y minisculas.

    """

    def __init__(self, path: Path):
        self.path = path if isinstance(path, Path) else Path(path)
        self.api_hash = self.data["api_hash"]
        self.api_id = self.data["api_id"]
        self.chat_id = self.data["chat_id"]
        super().__init__(worktable=self.data.get("worktable") or WORKTABLE)

    def _load_config(self) -> dict:
        """
        Devuelve las variables del archivo config. Cada key se devuelve en miniscula.
        """
        with open(self.path, "rt", encoding="utf-8") as fb:
            config = yaml.load(fb, Loader=yaml.UnsafeLoader)

        if config is None:
            raise Warning("El archivo config está vacio.")

        config_to_lower = {}
        for key, value in config.items():
            config_to_lower[key.lower().replace("-", "_")] = value
        return config_to_lower

    @property
    def data(self) -> dict:
        """atrajo para acceder al valor de '_'json_data'"""
        if hasattr(self, "_json_data") is False:
            json_data = self._load_config()
            setattr(self, "_json_data", json_data)
        return getattr(self, "_json_data")

    @property
    def session_string(self):
        return self.data.get("session_string")

    @property
    def exclude_words(self):
        return self.data.get("exclude_words")

    @property
    def exclude_ext(self):
        return self.data.get("exclude_ext")

    @property
    def min_size(self):
        return self.data.get("min_size")

    @property
    def max_size(self):
        return self.data.get("max_size")

    def save(self, sort_keys=False):
        config_to_upper = {}

        for key, value in self.data.items():
            config_to_upper[key.upper()] = value

        with open(self.path, "wt", encoding="utf-8") as fb:
            yaml.dump(config_to_upper, fb, sort_keys=sort_keys)
