
import os
import re

MINIMUM_SIZE_TO_BACKUP= 524288000
FILE_NAME_LENGTH_LIMIT = 54
FILESIZE_LIMIT = 2097152000
VERSION = "3.0"

EXT_YAML = ".yaml"
EXT_GZ = ".gz"
EXT_PICKLE = ".pickle"
EXT_JSON= ".json"
EXT_TAR= ".tar"
EXT_JZMA= ".xz"
EXT_JSON_XZ= EXT_JSON + EXT_JZMA

REGEX_PART_OF_FILEPART = re.compile(r'(?<=_)\d+-\d+')
REGEX_FILEPART_OF_STRING = re.compile("(?<=').*?(?=')")
PYTHON_DATA_TYPES= [int,float,str, bool, set, list, tuple,dict,type(None)]

PATH_CONFIG= input("Nombre del archivo config a usar>>>").strip().replace(".yaml","")+".yaml" if os.path.exists("debug.txt") else "config.yaml"
WORKTABLE= os.path.join("D:\.TEMP","toTelegram")  