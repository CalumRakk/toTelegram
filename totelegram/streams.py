import io
import logging
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


@contextmanager
def open_upload_source(path: Path, limit_rate_kbps: int):
    """Contexto manager que decide inteligentemente cómo entregar el archivo a Pyrogram."""
    if limit_rate_kbps > 0:
        limit_bytes = limit_rate_kbps * 1024
        with ThrottledFile(path, limit_bytes) as throttled_file:
            yield throttled_file
    else:
        # Se devuelve un string si el usuario no especifica un limite.
        yield str(path)


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
        self.name = path.name
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
