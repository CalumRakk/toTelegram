
import os
import re
from typing import List, Union

from humanfriendly import parse_size

from .config import Config
from .utils import get_all_files_from_directory
from .constants import EXT_JSON_XZ


REGEX_STRING_TO_LIST = re.compile('["\'].*?["\']|.*? ')


def string_to_list(string: Union[list, str]):
    """separa las palabras de un string en una lista

    Añade en una lista las palabras que esten separadas por espacio o
    entre comillas
    """
    #     default: en valor que se devolverá si string no es uno de los tipos validos
    # TODO: añadir pruebas de varios casos.
    if isinstance(string, list):
        return string
    elif isinstance(string, str):
        space_count = len(string.split())
        if space_count < 2:
            return [string]
        result = REGEX_STRING_TO_LIST.findall(string)
        return [s.strip().replace('"', "") for s in result]
    return string


def string_to_int(string: Union[int, str]):
    if isinstance(string, int):
        return string
    elif isinstance(string, str):
        return parse_size(string)
    return string


class ExclusionManager:
    def __init__(self, exclude_words=None, exclude_ext=None, min_size=None, max_size=None):
        config = Config()
        self.exclude_words = exclude_words or string_to_list(
            config.exclude_words)
        self.exclude_ext = exclude_ext or string_to_list(config.exclude_ext)
        self.min_size = min_size or string_to_int(config.min_size)
        self.max_size = max_size or string_to_int(config.max_size)
        # self.path_snapshot_files = string_to_int(config.path_snapshot_files)
        self.print()

    def print(self):
        print("\n[Argumentos de exclusión encontrados:")
        for key, value in self.__dict__.items():
            print(key, value)

    def exclusion_by_exists(self, path):
        """True si el archivo a subir ha generado un archivo `json.xz`
        """
        if os.path.exists(path + EXT_JSON_XZ):
            return True
        return False

    def exclusion_by_words(self, path) -> bool:
        """True si alguna de las palabras self.exclude_ext está dentro del nombre del archivo.
        Args:
            path: ruta absoluta del archivo.
        """
        if isinstance(self.exclude_words, list):
            ext = os.path.splitext(path)[1]
            name = os.path.basename(path).replace(ext, "")
            for word in self.exclude_words:
                if word in name:
                    return True
        return False

    def exclusion_by_ext(self, path: str) -> bool:
        """True si la extensión de path está dentro de la lista self.exclude_ext
        Args:
            path: ruta absoluta del archivo.
        """
        if isinstance(self.exclude_ext, list):
            ext = os.path.splitext(path)[1]
            if ext in self.exclude_ext:
                return True
        if path.endswith(EXT_JSON_XZ):
            return True
        return False

    def exclusion_by_min_size(self, path) -> bool:
        """True si path pesa menos que self.min_size
        Args:
            path: ruta absoluta del archivo.
        """
        if isinstance(self.min_size, int):
            filesize = os.path.getsize(path)
            if filesize < self.min_size:
                return True
        return False

    def exclusion_by_max_size(self, path: str) -> bool:
        """True si path pesa más que self.max_size
        Args:
            path: ruta absoluta del archivo.
        """
        if isinstance(self.max_size, int):
            filesize = os.path.getsize(path)
            if filesize > self.max_size:
                return True
        return False

    def filder(self, path: str) -> List[str]:
        """Le aplica todos los métodos de exclusion a path y devuelve una lista de path filtrada

        Args:
            path: ubicación de en el sistema de un archivo o carpeta.

        Raises:
            FileNotFoundError: En caso que path no exista.

        Returns:
            list: lista de path filtrada
        """
        if os.path.isdir(path):
            paths = get_all_files_from_directory(path)
        elif os.path.isfile(path):
            paths = [path]
        else:
            raise FileNotFoundError(path)
        methods = (self.exclusion_by_ext, self.exclusion_by_words,
                   self.exclusion_by_min_size, self.exclusion_by_max_size, self.exclusion_by_exists)
        filterCount = 0
        for path in paths[:]:
            for method in methods:
                if method(path):
                    paths.remove(path)
                    filterCount += 1
                    break
        print(f"Archivos filtrados: {filterCount}")
        return paths
