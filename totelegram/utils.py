import hashlib
import logging
import re
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from totelegram.setting import Settings

import filetype

logger = logging.getLogger(__name__)


def is_excluded(path: Path, settings: "Settings") -> bool:
    """Devuelve True si el path debe ser excluido según las reglas de exclusión."""
    logger.info(f"Comprobando path exclusion de {path=}")
    if not path.exists():
        logger.info(f"No existe: {path}, se omite")
        return True
    elif path.is_dir():
        logger.info(f"Es un directorio: {path}, se omite")
        return True
    elif settings.is_excluded(path):
        logger.info(f"Está excluido por configuración: {path}, se omite ")
        return True
    elif settings.is_excluded_default(path):
        logger.info(f"Está excluido por configuración: {path}, se omite ")
        return True
    return False


def create_md5sum_by_hashlib(path: Path):
    hash_md5 = hashlib.md5()
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
