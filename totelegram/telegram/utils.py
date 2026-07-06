import re
from typing import Union

SELF_CHAT_ALIASES = ["me", "mensajes guardados", "saved messages", "self"]
VALUE_NOT_SET = "NOT-SET"


def is_potential_username(value: str) -> bool:
    """Verifica si un string cumple con las reglas de un username de Telegram (sin @)."""
    return bool(re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{4,31}", str(value).strip()))


def is_direct_identifier(chat_id: Union[str, int, None]) -> bool:
    """Devuelve True si el chat_id es un identificador reconocido por Telegram."""
    if isinstance(chat_id, int):
        return True

    if chat_id is None:
        return False

    clean = str(chat_id).strip().lower()

    if clean.upper() == VALUE_NOT_SET:
        return False

    if clean.replace("-", "").isdigit():
        return True

    if clean.startswith("@"):
        return True

    if "t.me/" in clean or "telegram.me/" in clean:
        return True

    if clean in SELF_CHAT_ALIASES:
        return True

    return False


def normalize_chat_id(value: Union[str, int]) -> Union[int, str]:
    """Normaliza un chat_id para que sea compatible con las peticiones de Pyrogram."""
    if isinstance(value, int):
        return value

    raw = str(value).strip()
    if not raw or raw.upper() == VALUE_NOT_SET:
        return VALUE_NOT_SET

    if raw.lower() in SELF_CHAT_ALIASES:
        return "me"

    potential_number = re.sub(r"^(id:)", "", raw, flags=re.IGNORECASE)
    if re.fullmatch(r"-?\d+", potential_number):
        return int(potential_number)

    # Importamos dinámicamente para evitar problemas de rendimiento.
    from pyrogram.client import Client

    if Client.INVITE_LINK_RE.fullmatch(raw):
        return raw

    tme_match = re.search(r"t\.me/([a-zA-Z0-9_]{5,32})/?$", raw)
    if tme_match:
        return f"@{tme_match.group(1)}"

    if raw.startswith("@"):
        return raw.strip()

    raise ValueError(f"Invalid chat ID: {value}")
