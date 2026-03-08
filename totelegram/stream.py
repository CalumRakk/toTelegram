import hashlib
import io
from pathlib import Path

from tartape.stream import TapeVolume


class FileVolume(TapeVolume):
    def __init__(self, path: Path, start_offset: int, end_offset: int, name: str):
        super().__init__(name, end_offset - start_offset)
        self.path = path
        self.start_offset = start_offset
        self.end_offset = end_offset
        self._file = None
        self._position = 0
        self._md5 = hashlib.md5()
        self._closed = True

    @property
    def is_completed(self) -> bool:
        return self._position == self.size

    def _ensure_not_closed(self):
        if self._closed:
            raise ValueError("I/O operation on closed file volume.")

    def read(self, size: int = -1) -> bytes:  # type: ignore
        if self._closed:
            raise ValueError("File already closed")

        remaining = self.size - self._position
        if remaining <= 0:
            return b""

        # Determinar cuánto leer (size=-1 significa todo)
        bytes_to_read = remaining if (size < 0) else min(size, remaining)
        if not self._file:
            raise RuntimeError("File not opened")

        chunk = self._file.read(bytes_to_read)
        if not chunk:
            return b""

        self._position += len(chunk)
        self._md5.update(chunk)

        return chunk

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
        if self._position < self.size:
            raise RuntimeError(
                f"MD5 not available: Incomplete read ({self._position}/{self.size})."
            )
        return self._md5.hexdigest()

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
