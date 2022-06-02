import os.path
# GB (KB) binary

FILESIZE_LIMIT = 2097152000 #2147483648
EXT_YAML= ".yaml"
EXT_GZ= ".gz"
EXT_PICKLE= ".pickle"

WORKTABLE= ".temp"

PATH_FILEYAML_TEMPLATE= os.path.join("toTelegram","schema", "fileyaml_template.yaml")
PATH_FILEYAML_SCHEMA= os.path.join("toTelegram","schema", "fileyaml_schema.yaml")

PATH_CONFIG_FILE= os.path.join(".", "config.yaml")
PATH_CONFIG_TEMPLATE= os.path.join("toTelegram","schema", "config_template.yaml")
PATH_CONFIG_SCHEMA= os.path.join("toTelegram","schema", "config_schema.yaml")