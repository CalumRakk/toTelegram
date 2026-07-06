import io
import time


class ThrottledFile(io.BufferedIOBase):
    """
    Wrapper para limitar la velocidad de lectura de cualquier flujo binario.
    Hereda de io.BufferedIOBase para asegurar compatibilidad de tipos estándar.
    """

    def __init__(self, raw_stream: io.BufferedIOBase, speed_limit_bytes_per_s: int):
        self._stream = raw_stream
        self._speed_limit = speed_limit_bytes_per_s
        self._start_time = time.monotonic()
        self._bytes_read = 0

        self.name = getattr(raw_stream, "name", "unknown")
        self.mode = "rb"

    @property
    def md5sum(self):
        return getattr(self._stream, "md5sum", None)

    def read(self, size: int = -1) -> bytes:  # type: ignore
        chunk = self._stream.read(size)
        if not chunk:
            return b""

        self._bytes_read += len(chunk)

        if self._speed_limit > 0:
            elapsed = time.monotonic() - self._start_time

            if elapsed > 0:
                expected_time = self._bytes_read / self._speed_limit
                sleep_time = expected_time - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._stream.seek(offset, whence)

    def tell(self) -> int:
        return self._stream.tell()

    def close(self) -> None:
        return self._stream.close()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
