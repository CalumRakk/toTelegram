import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional

import filetype


def create_md5sum_by_hashlib(
    path: Path,
    chunk_size: int = 2 * 1024 * 1024,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> str:
    """Calcula el MD5 de un archivo emitiendo progreso por lotes."""
    hasher = hashlib.md5()
    total_size = path.stat().st_size
    current_bytes = 0

    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
            current_bytes += len(chunk)
            if on_progress:
                on_progress(current_bytes, total_size)

    return hasher.hexdigest()


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
