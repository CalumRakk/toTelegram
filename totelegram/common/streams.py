import hashlib
import io
import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Optional, cast

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


@contextmanager
def open_upload_source(path: Path, limit_rate_kbps: int):
    """Contexto manager que decide inteligentemente cómo entregar el archivo a Pyrogram."""
    if limit_rate_kbps > 0:
        limit_bytes = limit_rate_kbps * 1024
        with ThrottledFile(path, limit_bytes) as throttled_file:
            yield cast(BinaryIO, throttled_file)
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


class VirtualFileStream(io.BufferedIOBase):
    """
    Un stream virtual que expone un rango (start, end) de un archivo real
    como si fuera un archivo independiente, calculando el MD5 al vuelo.
    """

    def __init__(
        self,
        path: Path,
        start_offset: int,
        end_offset: int,
        filename: Optional[str] = None,
    ):
        if start_offset < 0 or end_offset < start_offset:
            raise ValueError(
                "Offsets invalidos: start_offset < 0 o end_offset < start_offset"
            )

        self.path = path
        self.start_offset = start_offset
        self.end_offset = end_offset
        self.size = end_offset - start_offset

        self._file = open(path, "rb")
        self._file.seek(start_offset)

        self._position = 0
        self._md5 = hashlib.md5()
        self._closed = False

        self.name = filename or path.name

    def read(self, size: int = -1) -> bytes:  # type: ignore
        if self._closed:
            raise ValueError("Operación de I/O sobre stream cerrado.")

        remaining = self.size - self._position
        if remaining <= 0:
            return b""

        # Determinar cuánto leer (size=-1 significa todo)
        bytes_to_read = remaining if (size < 0) else min(size, remaining)

        chunk = self._file.read(bytes_to_read)
        if not chunk:
            return b""

        self._position += len(chunk)
        self._md5.update(chunk)

        return chunk

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """Permite a Pyrogram mover el puntero si es necesario."""
        if whence == io.SEEK_SET:
            new_pos = offset
        elif whence == io.SEEK_CUR:
            new_pos = self._position + offset
        elif whence == io.SEEK_END:
            new_pos = self.size + offset
        else:
            raise ValueError("whence invalido")

        if new_pos < 0 or new_pos > self.size:
            raise ValueError("Seek fuera de los limites del rango.")

        self._position = new_pos
        self._file.seek(self.start_offset + new_pos)
        return self._position

    def tell(self) -> int:
        return self._position

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    @property
    def md5sum(self) -> str:
        if self._position < self.size:
            raise RuntimeError(
                f"MD5 no disponible: lectura incompleta ({self._position}/{self.size})."
            )
        return self._md5.hexdigest()

    def close(self):
        if not self._closed:
            self._file.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
