import os.path
# GB (KB) binary

FILESIZE = "2147483648"
EXT_YAML= ".yaml"
EXT_GZ= ".gz"
WORKTABLE= ".temp"

PATH_LOGGING_FILE= "logging.yaml"
PATH_LOGGING_TEMPLATE= os.path.join("client","schema", "logging_template.yaml")
PATH_LOGGING_SCHEMA= os.path.join("client","schema", "logging_schema.yaml")

PATH_CONFIG_FILE= os.path.join(".", "config.yaml")
PATH_CONFIG_TEMPLATE= os.path.join("client","schema", "config_template.yaml")
PATH_CONFIG_SCHEMA= os.path.join("client","schema", "config_schema.yaml")