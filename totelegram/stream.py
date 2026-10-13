import io
import logging
from pathlib import Path

from tartape.exceptions import InvalidOffsetError
from tartape.stream import FileSliceStream

logger = logging.getLogger(__name__)


class FileVolume(FileSliceStream):
    """Compatibility wrapper around TarTape's FileSliceStream with resilient seeking

    and automatic on-demand checksum fallback for Pyrogram upload workflows.
    """

    def __init__(
        self,
        path: Path | str,
        start_offset: int,
        end_offset: int,
        name: str | None = None,
    ):
        super().__init__(
            path=path,
            start_offset=start_offset,
            end_offset=end_offset,
            checksum_algorithm="md5",
            name=name,
        )

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self._ensure_open()

        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self._position + offset
        elif whence == io.SEEK_END:
            target = self.size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")

        if target < 0 or target > self.size:
            raise InvalidOffsetError(
                f"Seek position {target} out of slice bounds (0-{self.size})"
            )

        if target == self._position:
            return self._position

        # Resilient seeking logic
        if target == 0:
            # Rewinding to start resets in-flight hasher (the curious reader pattern)
            self._init_hasher()
            self._hash_cursor = 0
            self._integrity_broken = False
        elif target > self._hash_cursor:
            # Forward gap: skipped unread bytes break in-flight integrity
            self._integrity_broken = True
        # Note: target < self._hash_cursor is an overlapping retry;
        # read() will skip already-hashed bytes without breaking integrity.

        assert self._file is not None
        self._file.seek(self.start_offset + target)
        self._position = target
        return self._position

    def read(self, size: int = -1) -> bytes:
        self._ensure_open()
        if self._position >= self.size:
            return b""

        assert self._file is not None
        remaining = self.size - self._position
        bytes_to_read = (
            remaining if (size is None or size < 0) else min(size, remaining)
        )

        chunk = self._file.read(bytes_to_read)
        if not chunk:
            return b""

        # In-flight hash update ignoring re-read overlapping bytes
        if not self._integrity_broken and self._hasher is not None:
            start_pos = self._position
            end_pos = start_pos + len(chunk)

            if start_pos == self._hash_cursor:
                self._hasher.update(chunk)
                self._hash_cursor = end_pos
            elif end_pos > self._hash_cursor:
                # Overlapping retry: feed only unseen bytes beyond cursor
                offset_in_chunk = max(0, self._hash_cursor - start_pos)
                new_bytes = chunk[offset_in_chunk:]
                self._hasher.update(new_bytes)
                self._hash_cursor = end_pos

        self._position += len(chunk)
        return chunk

    @property
    def md5sum(self) -> str | None:
        """Return in-flight checksum if completed linearly, or compute on-demand as fallback."""
        val = super().md5sum
        if val is None:
            return self.compute_checksum(algorithm="md5")
        return val
