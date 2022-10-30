import os.path
import re

VERSION = "2.0"
FILESIZE_LIMIT = 2097152000
EXT_YAML = ".yaml"
EXT_GZ = ".gz"
EXT_PICKLE = ".pickle"
EXT_JSON= ".json"
EXT_TAR= ".tar"
EXT_JZMA= ".xz"
EXT_JSON_XZ= EXT_JSON + EXT_JZMA
MINIMUM_SIZE_TO_BACKUP= 524288000 # 500 MB

FILE_NOT_FOUND="Archivo no encontrado"
WORKTABLE = os.path.join("D:\.TEMP","toTelegram","test")  
FILE_NAME_LENGTH_LIMIT = 55 # NO CAMBIAR EL LIMITE. TODA LA LÓGICA DEPENDE DE ESTO.
PATH_CONFIG_FILE = os.path.join("setting", "config.yaml")

REGEX_PART_OF_FILEPART = re.compile(r'(?<=_)\d+-\d+')
REGEX_FILEPART_OF_STRING = re.compile("(?<=').*?(?=')")
REGEX_MD5SUM = re.compile(r'([a-f0-9]{32})')
REGEX_MESSAGE_LINK = re.compile(r'(?<=https://t.me/c/).*?(?=/)')
PYTHON_DATA_TYPES= [int,float,str, bool, set, list, tuple,dict,type(None)]

PATH_MD5SUM= os.path.join(WORKTABLE, "md5sums")
PATH_CHUNK= os.path.join(WORKTABLE, "chunks")
PATH_METADATA= os.path.join(WORKTABLE, "metadata")
PATH_BACKUPS= os.path.join(WORKTABLE, "backups")

if not os.path.exists(WORKTABLE): os.makedirs(WORKTABLE)
    
if not os.path.exists(PATH_MD5SUM): os.makedirs(PATH_MD5SUM)
    
if not os.path.exists(PATH_CHUNK): os.makedirs(PATH_CHUNK)

if not os.path.exists(PATH_METADATA): os.makedirs(PATH_METADATA)

if not os.path.exists(PATH_BACKUPS): os.makedirs(PATH_BACKUPS)


IGNORE_THESE_FILES= [".yaml",".json",".xml",".jpg",".png",".gif",".svg",".ico",".icov"]