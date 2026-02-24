import re

APP_NAME = "toTelegram"
CLI_BIN = "totelegram"
VALUE_NOT_SET = "NOT-SET"
ID_PREFIX_RE = re.compile(r"^id:", re.IGNORECASE)
SELF_CHAT_ALIASES = ["me", "mensajes guardados", "saved messages", "self"]


class Commands:
    PROFILE_CREATE = f"{CLI_BIN} profile create"
    PROFILE_DELETE = f"{CLI_BIN} profile delete"
    CONFIG_SET = f"{CLI_BIN} config set"
    CONFIG_SEARCH = f"{CLI_BIN} config search"
    CONFIG_EDIT_LIST = f"{CLI_BIN} config add/remove"
    CONFIG_ADD_LIST = f"{CLI_BIN} config add"
    CONFIG_REMOVE_LIST = f"{CLI_BIN} config remove"
    PROFILE_SWITCH = f"{CLI_BIN} profile switch"


class COLORS:
    INFO = "dim cyan"
    WARNING = "magenta"
    ERROR = "bold red"
    SUCCESS = "bold green"
    PROGRESS = "italic blue"

    # bold blue para títulos de tablas
    TABLE_TITLE = "bold blue"
