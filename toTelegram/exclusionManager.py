
import os
from .config import Config
from .functions import get_all_files_from_directory


class OptionalExclusionArguments:
    def __init__(self, exclude_words, exclude_ext, min_size, max_size):
        self.exclude_words = exclude_words
        self.exclude_ext = exclude_ext
        self.min_size = min_size
        self.max_size = max_size
    
    @classmethod
    def from_json(cls, json_data: dict):
        exclude_words = json_data["exclude_words"]
        exclude_ext = json_data["exclude_ext"]
        min_size = json_data["min_size"]
        max_size = json_data["max_size"]
        return OptionalExclusionArguments(exclude_words=exclude_words,
                                          exclude_ext=exclude_ext,
                                          min_size=min_size,
                                          max_size=max_size)


class ExclusionManager:
    config = Config

    def __init__(self, args):
        self.args = OptionalExclusionArguments.from_json(args.__dict__)

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
        if type(self.args.min_size) == int:
            filesize = os.path.getsize(path)
            if filesize < self.args.min_size:
                return True
        elif type(self.config.min_size) == int:
            filesize = os.path.getsize(path)
            if filesize < self.config.min_size:
                return True
        return False

    def _exclusion_by_max_size(self, path):
        if type(self.args.max_size) == int:
            filesize = os.path.getsize(path)
            if filesize > self.args.max_size:
                return True

        elif type(self.config.max_size) == int:
            filesize = os.path.getsize(path)
            if filesize > self.config.max_size:
                return True
        return False

    def is_skipped(self, path):
        if self._exclusion_by_ext(path):
            return True
        elif self._exclusion_by_words(path):
            return True
        elif self._exclusion_by_min_size(path):
            return True
        elif self._exclusion_by_max_size(path):
            return True
        return False

    def __call__(self, paths: list):
        """
        Devuelve una lista de path sin los archivos que cumplan alguno de los argumentos de exclusión.
        Parametros:
            paths (list of strings):
                Una lista de rutas de los archivos a filtrar
        """
        for path in paths[:]:
            if self.is_skipped(path):
                paths.remove(path)
        return path

    def filder(self, path):
        """
        Devuelve una lista de path filtrada.
        """
        if os.path.isfile(path):
            path = [path]
            return path
        elif os.path.isdir(path):
            paths = get_all_files_from_directory(path)
            for path in paths[:]:
                if self.is_skipped(path):
                    paths.remove(path)
            return path
        else:
            raise FileNotFoundError(path)
