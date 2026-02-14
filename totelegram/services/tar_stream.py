import hashlib
import io
import logging
from typing import List

from tartape import TarTape
from tartape.enums import TarEventType

logger = logging.getLogger(__name__)


class TapeInspector:
    @staticmethod
    def get_total_size(tape: TarTape) -> int:
        """Calcula el tamaño final del TAR basándose en el inventario."""
        total = 0
        for entry in tape._inventory.get_entries():
            total += 512  # Header
            if not entry.is_dir and not entry.is_symlink:
                padding = (512 - (entry.size % 512)) % 512
                total += entry.size + padding
        return total + 1024  # Footer


class TapeWindow:
    def __init__(self, start: int, end: int):
        self.start = start
        self.end = end
        self.cursor = 0  # Posición absoluta respecto al inicio de la cinta

    def process_chunk(self, data: bytes) -> bytes:
        """Toma un bloque de la cinta y devuelve solo lo que pertenece a la ventana."""
        chunk_len = len(data)
        current_offset = self.cursor
        self.cursor += chunk_len

        # ¿Hay solapamiento?
        overlap_start = max(current_offset, self.start)
        overlap_end = min(current_offset + chunk_len, self.end)

        if overlap_start < overlap_end:
            return data[overlap_start - current_offset : overlap_end - current_offset]
        return b""

    @property
    def is_finished(self) -> bool:
        return self.cursor >= self.end


class TarVolume(io.BufferedIOBase):
    def __init__(
        self,
        vol_index: int,
        tape: TarTape,
        start_offset: int,
        length: int,
        total_size: int,
    ):
        self.tape = tape
        self.start_offset = start_offset
        self.total_size = total_size

        # Atributos para Pyrogram
        self.size = min(length, total_size - start_offset)
        self.mode = "rb"
        self.name = f"volume_{vol_index}.tar"
        self.vol_index = vol_index

        self._reset()

    def _reset(self):
        """Prepara el estado inicial o reinicia para un seek(0)."""
        self._generator = self.tape.stream()
        self._segmenter = TapeWindow(self.start_offset, self.start_offset + self.size)
        self._buffer = bytearray()
        self._bytes_sent = 0
        self._md5 = hashlib.md5()
        self._completed_files = []

    def read(self, n: int = -1, /) -> bytes:  # type: ignore
        if self._bytes_sent >= self.size:
            return b""

        n = self.size if n is None else n
        limit = n if n > 0 else self.size

        # Llena el buffer desde el generador de la cinta
        while len(self._buffer) < limit:
            try:
                event = next(self._generator)
                self._handle_event(event)
            except StopIteration:
                break

        # Entrega solo lo que pide el consumidor y respeta el límite del volumen
        to_send = min(len(self._buffer), limit, self.size - self._bytes_sent)
        chunk = bytes(self._buffer[:to_send])

        self._buffer = self._buffer[to_send:]
        self._bytes_sent += len(chunk)
        self._md5.update(chunk)

        return chunk

    def _handle_event(self, event):
        """Decide qué hacer con cada evento de la cinta."""
        if event.type == TarEventType.FILE_DATA:
            data = self._segmenter.process_chunk(event.data)
            if data:
                self._buffer.extend(data)
        elif event.type == TarEventType.FILE_END:
            # Si el archivo terminó y el segmentador ya pasó su offset,
            # significa que este volumen contiene el final del archivo.
            if self._segmenter.cursor >= self.start_offset:
                self._completed_files.append(
                    {
                        "path": event.entry.path,
                        "start_offset": event.entry.offset,
                        "end_offset": self._segmenter.cursor,
                        "start_vol": self._segmenter.start,
                        "end_vol": self.vol_index,
                    }
                )

    def tell(self) -> int:
        return self._bytes_sent

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == io.SEEK_END:
            return self.size
        if whence == io.SEEK_SET and offset == 0:
            self._reset()
            return 0
        raise io.UnsupportedOperation("Solo se soporta seek(0) o SEEK_END")

    def get_md5(self) -> str:
        return self._md5.hexdigest()

    def get_completed_files(self) -> tuple[str, List[dict]]:
        return self._md5.hexdigest(), self._completed_files
