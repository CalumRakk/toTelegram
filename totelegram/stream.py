import hashlib
import io
import logging
from pathlib import Path

from tartape.stream import TapeVolume

logger = logging.getLogger(__name__)


class FileVolume(TapeVolume):
    def __init__(self, path: Path, start_offset: int, end_offset: int, name: str):
        super().__init__(name, end_offset - start_offset)
        self.path = path
        self.start_offset = start_offset
        self.end_offset = end_offset
        self._file = None
        self._position = 0

        # Lógica de Integridad
        self._md5_context = hashlib.md5()
        self._hash_cursor = 0
        self._integrity_broken = False
        self._final_md5 = None
        self._closed = True

    @property
    def is_completed(self) -> bool:
        return self._position == self.size

    def _ensure_not_closed(self):
        if self._closed:
            raise ValueError("I/O operation on closed file volume.")

    def read(self, size: int = -1) -> bytes:  # type: ignore
        if self._closed:
            raise ValueError("I/O operation on closed file volume.")

        if self._file is None:
            raise RuntimeError("File not opened")

        # conoce posición real ANTES de la lectura
        current_relative_pos = self._file.tell() - self.start_offset

        remaining = self.size - current_relative_pos
        if remaining <= 0:
            return b""

        bytes_to_read = remaining if (size < 0) else min(size, remaining)
        chunk = self._file.read(bytes_to_read)

        if not chunk:
            return b""

        if not self._integrity_broken:
            if current_relative_pos == self._hash_cursor:
                self._md5_context.update(chunk)
                self._hash_cursor += len(chunk)

            # Se leyó algo más allá de lo que teníamos hasheado. Integridad rota.
            elif current_relative_pos > self._hash_cursor:
                logger.debug(
                    f"Integridad on-the-fly rota en {self.name}: Salto detectado de {self._hash_cursor} a {current_relative_pos}"
                )
                self._integrity_broken = True

            # Re-lectura o Retroceso (Rewind/Retry)
            else:
                # Comprobamos si esta re-lectura termina aportando algo nuevo al cursor
                new_data_start = self._hash_cursor - current_relative_pos
                if len(chunk) > new_data_start:
                    # Una parte es vieja y una parte es nueva (Overlap)
                    # Esto puede pasar si un chunk de reintento es más grande que el original
                    extra_data = chunk[new_data_start:]
                    self._md5_context.update(extra_data)
                    self._hash_cursor += len(extra_data)

        self._position = self._file.tell() - self.start_offset
        return chunk

    def _calculate_manually(self) -> str:
        logger.info(
            f"Calculando MD5 manual para {self.name} (Integridad on-the-fly no garantizada)."
        )
        hasher = hashlib.md5()
        with open(self.path, "rb") as f:
            f.seek(self.start_offset)
            remaining = self.size
            while remaining > 0:
                chunk = f.read(min(remaining, 1024 * 1024))
                if not chunk:
                    break
                hasher.update(chunk)
                remaining -= len(chunk)
        return hasher.hexdigest()

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            new_pos = offset
        elif whence == io.SEEK_CUR:
            new_pos = self._position + offset
        elif whence == io.SEEK_END:
            new_pos = self.size + offset
        else:
            raise ValueError("whence invalid")

        if new_pos < 0 or new_pos > self.size:
            raise ValueError("Seek out of bounds")

        self._position = new_pos
        if not self._file:
            raise RuntimeError("File not opened")

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
        if self._final_md5:
            return self._final_md5

        if not self._integrity_broken and self._hash_cursor == self.size:
            self._final_md5 = self._md5_context.hexdigest()
            return self._final_md5

        self._final_md5 = self._calculate_manually()
        return self._final_md5

    def close(self):
        self.__exit__(None, None, None)

    def open(self):
        self.__enter__()

    def __enter__(self):
        if self._closed:
            self._file = open(self.path, "rb")
            self._file.seek(self.start_offset)
            self._closed = False
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._closed and self._file:
            self._file.close()
            self._closed = True
