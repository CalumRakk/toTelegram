import os.path
import re

VERSION = "2.0"
FILESIZE_LIMIT = 2097152000 # 26214400 # 2097152000 #2500000 #2147483648
EXT_YAML = ".yaml"
EXT_GZ = ".gz"
EXT_PICKLE = ".pickle"
EXT_JSON= ".json"
EXT_TAR= ".tar"

FILE_NOT_FOUND="Archivo no encontrado"
WORKTABLE = os.path.join("D:\.TEMP","toTelegram","test") 
BACKUP= os.path.join(WORKTABLE,"backups") 
# NO CAMBIAR EL LIMITE. TODA LA LÓGICA DEPENDE DE ESTO.
FILE_NAME_LENGTH_LIMIT = 55
PATH_CONFIG_FILE = os.path.join(".", "config.yaml")

REGEX_PART_OF_FILEPART = re.compile(r'(?<=_)\d+-\d+')
REGEX_FILEPART_OF_STRING = re.compile("(?<=').*?(?=')")
REGEX_MD5SUM = re.compile(r'([a-f0-9]{32})')
REGEX_MESSAGE_LINK = re.compile(r'(?<=https://t.me/c/).*?(?=/)')

if not os.path.exists(WORKTABLE):
    os.makedirs(WORKTABLE)
