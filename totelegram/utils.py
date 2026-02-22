import hashlib
import keyword
import logging
import os
import re
import sys
from contextlib import nullcontext
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING, Any, List, Union, cast

from totelegram.console import console
from totelegram.core.consts import APP_NAME, SELF_CHAT_ALIASES, VALUE_NOT_SET

if TYPE_CHECKING:
    from totelegram.core.setting import Settings

import filetype

logger = logging.getLogger(__name__)



if sys.version_info >= (3, 12):
    from itertools import batched
else:

    def batched(iterable: list[Any], n):
        """Fallback para Python < 3.12"""
        import itertools

        it = iter(iterable)
        while batch := list(itertools.islice(it, n)):
            yield batch


def is_excluded(path: Path, patterns: List[str]) -> bool:
    """Devuelve True si el path debe ser excluido según las reglas de exclusión."""
    logger.info(f"Comprobando path exclusion de {path=}")
    if not path.exists():
        logger.info(f"No existe: {path}, se omite")
        return True
    elif path.is_dir():
        logger.info(f"Es un directorio: {path}, se omite")
        return True

    for pattern in patterns:
        # Para coincidencia directa (archivo o carpeta exacta)
        if path.match(pattern):
            return True

        # Para coincidencia recursiva (si una carpeta padre está excluida)
        for parent in path.parents:
            if str(parent) == ".":
                break
            if parent.match(pattern):
                return True
    return False


def create_md5sum_by_hashlib(path: Path):
    """
    Calcula el MD5 de un archivo. Si el archivo es grande (>100MB),
    muestra un mensaje de estado en la consola.
    """
    hash_md5 = hashlib.md5()
    file_size = path.stat().st_size

    # Solo mostramos el status si el archivo es lo suficientemente grande
    # 100MB
    threshold = 100 * 1024 * 1024

    status_context = (
        console.status(f"[dim]Procesando firma digital (MD5): {path.name}...[/dim]")
        if file_size > threshold
        else nullcontext()
    )

    with status_context:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(50 * 1024 * 1024), b""):
                hash_md5.update(chunk)

    return hash_md5.hexdigest()


def get_mimetype(path: Path):
    kind = filetype.guess(path)
    if kind is None:
        # Si filetype no puede adivinar, asumimos que es un flujo de bytes genérico.
        # Esto evita el crash en base de datos y permite subir archivos "raros"
        return "application/octet-stream"
    return kind.mime


def normalize_windows_name(name: str) -> str:
    invalid_chars = r'[<>:"/\\|?*\x00-\x1F]'
    name = re.sub(invalid_chars, "_", name)
    name = name.rstrip(" .")
    if len(name) < 0:
        raise ValueError("Invalid name")
    return name


def sleep_progress(seconds: float):
    total = int(seconds)
    if total <= 0:
        return

    logger.info(
        f"Esperando {total // 60} minutos y {total % 60} segundos antes de continuar..."
    )

    for i in range(total, 0, -1):
        sleep(1)
        if i % 60 == 0:
            mins_left = i // 60
            logger.info(f"Faltan {mins_left} minutos...")
        elif i <= 10:  # Mostrar segundos finales
            logger.info(f"{i} segundos restantes...")


def get_user_config_dir(app_name: str = APP_NAME) -> Path:
    if sys.platform.startswith("win"):
        # En Windows se usa APPDATA → Roaming
        return Path(cast(str, os.getenv("APPDATA"))) / app_name
    elif sys.platform == "darwin":
        # En macOS
        return Path.home() / "Library" / "Application Support" / app_name
    else:
        # En Linux / Unix
        return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / app_name


def is_direct_identifier(chat_id: Union[str, int, None]) -> bool:
    """Devuelve True si el chat_id es un identificador de pyrogram/telegram.

    Para el caso de VALUE_NOT_SET, devuelve False
    """
    if isinstance(chat_id, int):
        return True

    if chat_id is None:
        return False

    clean = str(chat_id).strip().lower()

    # Aunque terminaria como False, se especifica para hacerlo explicito.
    if clean == VALUE_NOT_SET:
        return False

    # IDs numéricos (incluyendo negativos)
    if clean.replace("-", "").isdigit():
        return True

    # Usernames oficiales
    if clean.startswith("@"):
        return True

    # Enlaces directos
    if "t.me/" in clean or "telegram.me/" in clean:
        return True

    # Aliases internos del sistema
    if clean in SELF_CHAT_ALIASES:
        return True

    return False


def normalize_chat_id(value: Union[str, int]) -> Union[int, str]:
    """Normaliza un chat_id para que sea compatible con pyrogram/telegram."""
    if isinstance(value, int):
        return value

    raw = str(value).strip()
    if not raw or raw.upper() == VALUE_NOT_SET:
        return VALUE_NOT_SET

    # Identidad propia
    if raw.lower() in SELF_CHAT_ALIASES:
        return "me"

    # ID Numérico. Limpiamos posible prefijo "ID:" o "id:"
    potential_number = re.sub(r"^(id:)", "", raw, flags=re.IGNORECASE)
    if re.fullmatch(r"-?\d+", potential_number):
        return int(potential_number)

    # Enlaces de Telegram (Invite links o Username links)
    from pyrogram.client import Client

    if Client.INVITE_LINK_RE.fullmatch(raw):
        return raw

    # Probamos si es un enlace de tipo t.me/username
    tme_match = re.search(r"t\.me/([a-zA-Z0-9_]{5,32})/?$", raw)
    if tme_match:
        return f"@{tme_match.group(1)}"

    # Usernames (@username)
    # clean_username = raw.lstrip("@")
    # if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", clean_username):
    #     return f"@{clean_username}"
    if raw.startswith("@"):
        return raw.strip()

    raise ValueError(f"Invalid chat ID: {value}")


def is_valid_profile_name(profile_name: str):
    return profile_name.isidentifier() and not keyword.iskeyword(profile_name)
