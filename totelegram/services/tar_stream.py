import hashlib
import io
import logging

from tartape.enums import TarEventType
from tartape.schemas import TarFileEndEvent

logger = logging.getLogger(__name__)


class TapeWindow:
    def __init__(self, start: int, end: int):
        self.start = start
        self.end = end
        self.cursor = 0  # Posición absoluta respecto al inicio de la cinta

    def get_intersection(self, data: bytes) -> bytes:
        """
        Calcula la intersección entre el chunk de datos actual y la ventana.
        Retorna bytes vacíos si no hay solapamiento.
        """
        chunk_len = len(data)
        chunk_start = self.cursor
        chunk_end = self.cursor + chunk_len

        # El cursor siempre avanza, simulando el paso del tiempo/cinta
        self.cursor += chunk_len

        # Si el chunk es "historia antigua", lo ignoramos rápido
        # Si el chunk es "futuro lejano", lo ignoramos rápido
        if chunk_end <= self.start or chunk_start >= self.end:
            return b""

        # Cálculo de Recorte
        overlap_start = max(chunk_start, self.start)
        overlap_end = min(chunk_end, self.end)

        if overlap_start < overlap_end:
            slice_start = overlap_start - chunk_start
            slice_end = overlap_end - chunk_start
            return data[slice_start:slice_end]

        return b""

    def is_past(self, offset: int) -> bool:
        """¿Este offset ya quedó atrás de nuestra ventana?"""
        return offset >= self.start


class TarVolumeStream(io.BufferedIOBase):
    """
    La grabadora física. Maneja el estado, el buffer y el MD5.
    Usa una TapeWindow para filtrar la cinta.
    """

    def __init__(self, generator, start_offset: int, length: int, vol_index: int):
        self.generator = generator
        self.window = TapeWindow(start_offset, start_offset + length)
        self.vol_limit = length
        self.vol_index = vol_index

        self.bytes_yielded = 0
        self._buffer = bytearray()
        self._md5 = hashlib.md5()
        self._eof = False

        self._active_files = {}
        self._completed_entries = []

        self.name = f"vol_{vol_index}.tar"
        self._virtual_cursor = 0

    def read(self, size: int | None = -1, /) -> bytes:
        """Método estándar de Python para leer bytes (lo usa Pyrogram)."""
        if self.bytes_yielded >= self.vol_limit or self._eof:
            return b""

        assert size is not None

        wanted = size if size > 0 else self.vol_limit

        # Llena el buffer si es necesario
        while len(self._buffer) < wanted and not self._eof:
            self._advance_tape()

        # Recortar para no exceder el límite del volumen
        remaining = self.vol_limit - self.bytes_yielded
        take = min(wanted, len(self._buffer), remaining)

        chunk = bytes(self._buffer[:take])
        self._buffer = self._buffer[take:]  # Desplaza el buffer

        self.bytes_yielded += len(chunk)
        self._md5.update(chunk)
        return chunk

    def seekable(self) -> bool:
        """
        Decimos que sí somos 'seekable' para que Pyrogram
        pueda ejecutar su lógica de cálculo de tamaño (seek al final -> tell -> seek al inicio).
        """
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        """
        Implementación 'Fake' de seek.
        Solo soporta la lógica de cálculo de tamaño antes de empezar a leer.
        """
        # SEEK_END (2): Pyrogram quiere ir al final para ver el tamaño
        if whence == 2:
            # Simulamos que estamos al final
            self._virtual_cursor = self.vol_limit + offset
            return self._virtual_cursor

        # SEEK_SET (0): Pyrogram quiere volver al inicio
        if whence == 0:
            if offset == 0:
                # Verificación de seguridad:
                # Solo permitimos volver al inicio si NO hemos emitido datos reales aún.
                if self.bytes_yielded > 0:
                    raise io.UnsupportedOperation(
                        "No se puede rebobinar un stream ya iniciado."
                    )

                self._virtual_cursor = 0
                return 0

        raise io.UnsupportedOperation(
            f"Seek no soportado: offset={offset}, whence={whence}"
        )

    def tell(self) -> int:
        """Devuelve la posición (real o simulada)."""
        # Si estamos en medio de la danza del seek simulado, devolvemos el cursor virtual
        if self._virtual_cursor > self.bytes_yielded:
            return self._virtual_cursor

        # Si estamos leyendo de verdad, devolvemos lo que hemos entregado
        return self.bytes_yielded

    def _advance_tape(self):
        """Consume un evento y lo pasa por la ventana."""
        try:
            event = next(self.generator)

            if event.type == TarEventType.FILE_START:
                # Registramos dónde empieza este archivo en la cinta global
                self._active_files[event.entry.arc_path] = {
                    "offset": self.window.cursor,
                    "vol": self.window.cursor // self.vol_limit,
                }

            elif event.type == TarEventType.FILE_DATA:
                # La ventana decide si estos datos nos sirven
                useful_data = self.window.get_intersection(event.data)
                if useful_data:
                    self._buffer.extend(useful_data)

            elif event.type == TarEventType.FILE_END:
                self._handle_file_completion(event)

            elif event.type == TarEventType.TAPE_COMPLETED:
                self._eof = True
        except StopIteration:
            self._eof = True

    def _handle_file_completion(self, event: TarFileEndEvent):
        """Si un archivo termina, verificamos si es relevante para el índice."""
        path = event.entry.arc_path
        if path in self._active_files:
            info = self._active_files.pop(path)

            if self.window.is_past(self.window.cursor):
                self._completed_entries.append(
                    {
                        "path": path,
                        "start_offset": info["offset"],
                        "end_offset": self.window.cursor,
                        "start_vol": info["vol"],
                        "end_vol": self.vol_index,
                    }
                )

    def get_results(self):
        return self._md5.hexdigest(), self._completed_entries
