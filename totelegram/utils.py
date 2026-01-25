import hashlib
import logging
import re
import sys
from contextlib import nullcontext
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING, Any, List

from totelegram.console import console

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
