import hashlib
import os
import re
import sys
from pathlib import Path

import filetype


def create_md5sum_by_hashlib(path: Path) -> str:
    """
    Calcula el MD5 de un archivo por bloques de forma eficiente.
    Esta utilidad es pura y libre de efectos colaterales en consola.
    """
    hash_md5 = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(50 * 1024 * 1024), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def get_mimetype(path: Path) -> str:
    """Determina el tipo MIME de un archivo."""
    kind = filetype.guess(path)
    if kind is None:
        return "application/octet-stream"
    return kind.mime


def normalize_windows_name(name: str) -> str:
    """Sanitiza nombres de archivo para sistemas Windows."""
    invalid_chars = r'[<>:"/\\|?*\x00-\x1F]'
    name = re.sub(invalid_chars, "_", name)
    name = name.rstrip(" .")
    if not name:
        raise ValueError("Invalid name")
    return name


def get_user_config_dir(app_name: str) -> Path:
    """Devuelve la ruta del directorio de configuración según el sistema operativo."""
    if sys.platform.startswith("win"):
        return Path(os.getenv("APPDATA", "")) / app_name
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    else:
        return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / app_name
