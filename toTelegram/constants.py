import os.path

VERSION= "0.0.2"
FILESIZE_LIMIT = 2097152000 # 2500000 #  2097152000 #2147483648
EXT_YAML = ".yaml"
EXT_GZ = ".gz"
EXT_PICKLE = ".pickle"

WORKTABLE = ".temp"
# NO CAMBIAR EL LIMITE. TODA LA LÓGICA DEPENDE DE ESTO.
FILE_NAME_LENGTH_LIMIT=55
PATH_CONFIG_FILE = os.path.join(".", "config.yaml")

if not os.path.exists(WORKTABLE):
    os.mkdir(WORKTABLE)