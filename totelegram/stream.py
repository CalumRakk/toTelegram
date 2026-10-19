import logging
from pathlib import Path

from tartape.stream import FileSliceStream

logger = logging.getLogger(__name__)


class FileVolume(FileSliceStream):
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

    @property
    def md5sum(self) -> str | None:
        # TODO: Funcion heredada. Esta propiedad no refleja una simple propiedad.
        val = self.checksum
        if val is None:
            return self.compute_checksum(algorithm="md5")
        return val
