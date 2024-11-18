import os
import re
from pathlib import Path
from platform import system
from os import getenv

MINIMUM_SIZE_TO_BACKUP: int = 524288000
"""El tamaño minimo en bytes que debe tener un backup para ser subido."""
FILE_NAME_LENGTH_LIMIT: int = 55
"""El tamaño maximo de nombre de archivo. Si un archivo excede este limite
su nombre de archivo será su md5sum más su extension de archivo."""
FILESIZE_LIMIT: int = 2097152000
"""El tamaño maximo en bytes que acepta Telegram para subir un archivo"""
VERSION = "3.0"

EXT_YAML = ".yaml"
EXT_GZ = ".gz"
EXT_PICKLE = ".pickle"
EXT_JSON = ".json"
EXT_TAR = ".tar"
EXT_JZMA = ".xz"
EXT_JSON_XZ = EXT_JSON + EXT_JZMA

REGEX_PART_OF_FILEPART = re.compile(r"(?<=_)\d+-\d+")
REGEX_FILEPART_OF_STRING = re.compile("(?<=').*?(?=')")
PYTHON_DATA_TYPES = [int, float, str, bool, set, list, tuple, dict, type(None)]

PROJECT_NAME = "toTelegram"
HOME = Path.home() / ".local/share" if system() == "Linux" else Path(getenv("APPDATA"))
WORKTABLE = HOME / PROJECT_NAME

WORKTABLE.mkdir(parents=True, exist_ok=True)
