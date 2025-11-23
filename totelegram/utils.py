import hashlib
import io
import logging
import re
import time
from pathlib import Path
from time import sleep

import filetype

logger = logging.getLogger(__name__)


class ThrottledFile(io.BufferedIOBase):
    """
    Wrapper para limitar la velocidad de lectura.
    Hereda de io.BufferedIOBase para pasar todas las validaciones de tipo (isinstance).
    """

    def __init__(self, path: Path, speed_limit_bytes_per_s: int):
        self._path = path
        self._speed_limit = speed_limit_bytes_per_s
        self._file = open(path, "rb")
        self._start_time = time.monotonic()
        self._bytes_read = 0

        # Atributos esenciales para Pyrogram
        self.name = str(path)
        self.mode = "rb"

    def read(self, size: int = -1) -> bytes:  # type: ignore
        # Si size es -1 o None, leer todo (comportamiento estándar)
        if size is None or size < 0:
            chunk = self._file.read()
        else:
            chunk = self._file.read(size)

        if not chunk:
            return b""

        self._bytes_read += len(chunk)

        if self._speed_limit > 0:
            elapsed = time.monotonic() - self._start_time
            # Evitar división por cero
            if elapsed > 0:
                expected_time = self._bytes_read / self._speed_limit
                sleep_time = expected_time - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._file.seek(offset, whence)

    def tell(self) -> int:
        return self._file.tell()

    def fileno(self) -> int:
        return self._file.fileno()

    def close(self) -> None:
        return self._file.close()

    def flush(self) -> None:
        return self._file.flush()

    @property
    def closed(self) -> bool:
        return self._file.closed

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


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
