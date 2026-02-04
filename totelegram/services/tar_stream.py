import hashlib
import io
import logging
from typing import Dict, List

from tartape.enums import TarEventType
from tartape.schemas import TarEvent

logger = logging.getLogger(__name__)


class TarVolumeBridge(io.BufferedIOBase):
    """
    Filtro de bytes que convierte el flujo de eventos de tartape en
    un stream lineal para Telegram, gestionando volumenes y offsets.
    """

    def __init__(
        self, generator, start_offset: int, max_length: int, volume_index: int
    ):
        self.generator = generator
        self.start_at = start_offset  # Offset global donde empieza este volumen
        self.limit = max_length  # Tamaño máximo de este volumen
        self.vol_index = volume_index  # Indice del volumen actual (0, 1, 2...)

        # Estado del Stream
        self.global_ptr = 0  # Puntero absoluto en la cinta TAR
        self.bytes_sent = 0  # Bytes entregados en este volumen
        self.buffer = bytearray()
        self.hasher = hashlib.md5()
        self.eof_reached = False

        # Seguimiento de Metadatos (Commit Atómico)
        self.active_files: Dict[str, dict] = {}  # Archivos que han empezado
        self.completed_entries: List[dict] = []  # Archivos terminados en este volumen

    def read(self, size: int | None = -1, /) -> bytes:
        """
        Método principal que consume Pyrogram.
        Extrae bytes del generador hasta llenar el 'size' o alcanzar el 'limit'.
        """
        if self.bytes_sent >= self.limit or self.eof_reached:
            return b""

        assert isinstance(size, int), "size debe ser un entero"

        # Si el buffer está vacío, pedimos más al generador
        while not self.buffer and not self.eof_reached:
            self._consume_generator()

        # Calculamos cuánto podemos entregar sin pasar el límite del volumen
        remaining_vol = self.limit - self.bytes_sent
        to_read = min(
            len(self.buffer), size if size > 0 else len(self.buffer), remaining_vol
        )

        chunk = bytes(self.buffer[:to_read])
        self.buffer = self.buffer[to_read:]

        self.bytes_sent += len(chunk)
        self.hasher.update(chunk)

        return chunk

    def _consume_generator(self):
        """
        Consume el siguiente evento del generador TAR y lo procesa
        según la posición global del puntero.
        """
        try:
            event: TarEvent = next(self.generator)

            if event.type == TarEventType.FILE_START:
                # Anotamos dónde empieza este archivo en la cinta global
                self.active_files[event.entry.arc_path] = {
                    "start_offset": self.global_ptr,
                    "start_vol": (
                        self.vol_index
                        if self.global_ptr >= self.start_at
                        else self._calculate_start_vol(self.global_ptr)
                    ),
                }

            elif event.type == TarEventType.FILE_DATA:
                data_len = len(event.data)

                # ¿Estos bytes caen dentro de nuestro volumen?
                chunk_start = self.global_ptr
                chunk_end = self.global_ptr + data_len

                # Si el chunk solapa con nuestra ventana de interés [start_at, start_at + limit]
                overlap_start = max(chunk_start, self.start_at)
                overlap_end = min(chunk_end, self.start_at + self.limit)

                if overlap_start < overlap_end:
                    # Extraemos solo la porción que cae dentro del volumen
                    relative_start = overlap_start - chunk_start
                    relative_end = overlap_end - chunk_start
                    self.buffer.extend(event.data[relative_start:relative_end])

                self.global_ptr += data_len

            elif event.type == TarEventType.FILE_END:
                path = event.entry.arc_path
                if path in self.active_files:
                    info = self.active_files.pop(path)

                    # Si el archivo terminó dentro o después de que empezamos este volumen
                    if self.global_ptr >= self.start_at:
                        self.completed_entries.append(
                            {
                                "path": path,
                                "start_offset": info["start_offset"],
                                "end_offset": self.global_ptr,
                                "start_vol": info["start_vol"],
                            }
                        )

            elif event.type == TarEventType.TAPE_COMPLETED:
                self.eof_reached = True

        except StopIteration:
            self.eof_reached = True

    def _calculate_start_vol(self, offset: int) -> int:
        return offset // self.limit

    def get_volume_md5(self) -> str:
        return self.hasher.hexdigest()

    def get_completed_files(self) -> List[dict]:
        return self.completed_entries
