import hashlib
import io
import logging
from typing import List

from tartape import TarTape
from tartape.enums import TarEventType
from tartape.schemas import TarEvent

logger = logging.getLogger(__name__)


class TapeInspector:
    # TODO: mover esta logica a tartape
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


class TapeRecorder:
    """
    La grabadora de Cinta.
    Su trabajo es observar la Cinta Maestra y presionar REC/STOP en los
    momentos precisos para capturar un segmento exacto.
    """

    def __init__(self, start: int, end: int):
        self.start = start
        self.end = end
        self.cursor = 0  # El contador de la cinta (minutero de bytes)

    def capture(self, master_data: bytes) -> bytes:
        """
        Observa un bloque de la Cinta Maestra y recorta la porción
        que pertenece al volumen actual.
        """
        chunk_len = len(master_data)
        chunk_start = self.cursor
        self.cursor += chunk_len

        # ¿El bloque actual toca nuestra ventana de grabación?
        overlap_start = max(chunk_start, self.start)
        overlap_end = min(chunk_start + chunk_len, self.end)

        if overlap_start < overlap_end:
            # Calculamos el recorte (slice) relativo al bloque actual
            slice_start = overlap_start - chunk_start
            slice_end = overlap_end - chunk_start
            return master_data[slice_start:slice_end]

        return b""


class TarVolume(io.BufferedIOBase):
    """
    Representa un volumen específico de una cinta TAR virtual.

    Esta clase actúa como un adaptador 'file-like' para Pyrogram, permitiendo que
    una carpeta indexada sea enviada en múltiples mensajes de Telegram sin
    necesidad de crear archivos temporales en el disco.
    """

    def __init__(
        self,
        tape: "TarTape",
        start_offset: int,
        max_volume_size: int,
        total_tape_size: int,
        vol_index: int,
    ):
        """
        Args:
            tape: Instancia de TarTape que contiene el inventario y el generador.

            start_offset: El punto de inicio (en bytes) dentro de la cinta global
                          donde comienza este volumen.
                          Comienza en 0 y se incrementa con el tamaño de cada volumen enviado.
                          Ejemplo: Si es el segundo volumen de 2GB, será 2147483648.


            max_volume_size: El tamaño máximo (en bytes) que queremos para este volumen.
                             Normalmente definido por el límite de Telegram (2GB o 4GB).

            total_tape_size: El tamaño total calculado de toda la cinta (todos los archivos
                             sumados con sus cabeceras TAR). Sirve para que el último
                             volumen sepa exactamente cuándo detenerse.

            vol_index: Índice del volumen (0 para el primero, 1 para el segundo, etc.)
        """
        self.tape = tape
        self.start_offset = start_offset
        self.total_tape_size = total_tape_size

        # El tamaño real de este volumen es el máximo solicitado, a menos que
        # lo que quede de cinta sea menor (en cuyo caso, este es el último volumen).
        self.size = min(max_volume_size, total_tape_size - start_offset)

        # Atributos para Pyrogram
        self.mode = "rb"
        self.name = f"volume_{vol_index}.tar"

        self.vol_index = vol_index

        self._active_files = {}
        self._completed_entries = []

        self._load_new_cassette()

    @property
    def has_recording_ended(self) -> bool:
        """Indica si la cinta maestra ya finalizo."""
        return self.total_tape_size >= self._recorder.cursor

    def read(self, n: int = -1, /) -> bytes:  # type: ignore
        if self._bytes_recorded >= self.size:
            return b""

        n = self.size if n is None else n
        limit = n if n > 0 else self.size

        # Llena el buffer desde el generador de la cinta
        while len(self._buffer) < limit:
            try:
                event = next(self._master_stream)
                self._record_event(event)
            except StopIteration:
                break

        # Entrega solo lo que pide el consumidor y respeta el límite del volumen
        to_send = min(len(self._buffer), limit, self.size - self._bytes_recorded)
        chunk = bytes(self._buffer[:to_send])

        self._buffer = self._buffer[to_send:]
        self._bytes_recorded += len(chunk)
        self._md5.update(chunk)

        return chunk

    def get_md5(self) -> str:
        return self._md5.hexdigest()

    def get_completed_files(self) -> tuple[str, List[dict]]:
        return self._md5.hexdigest(), self._completed_files

    def _load_new_cassette(self):
        """Prepara el equipo para grabar desde el principio del segmento."""
        self._master_stream = self.tape.stream()
        self._recorder = TapeRecorder(self.start_offset, self.start_offset + self.size)
        self._buffer = bytearray()
        self._bytes_recorded = 0
        self._md5 = hashlib.md5()
        self._completed_files = []

    def _record_event(self, event: TarEvent):
        """Procesa los eventos de la cinta maestra."""
        if event.type == TarEventType.FILE_START:
            self._active_files[event.entry.arc_path] = {
                "offset": self._recorder.cursor,
                "vol": self.vol_index,
            }
        elif event.type == TarEventType.FILE_DATA:
            segment = self._recorder.capture(event.data)
            if segment:
                self._buffer.extend(segment)

        elif event.type == TarEventType.FILE_END:
            # Si el archivo terminó y el segmentador ya pasó su offset,
            # significa que este volumen contiene el final del archivo.
            path = event.entry.arc_path
            if path in self._active_files:
                info = self._active_files.pop(path)
                if self._recorder.cursor >= self.start_offset:
                    self._completed_files.append(
                        {
                            "path": event.entry.arc_path,
                            "start_offset": info["offset"],
                            "end_offset": self._recorder.cursor,
                            "start_vol": self._recorder.start,
                            "end_vol": self.vol_index,
                        }
                    )

    # Métodos para cumplir con la interfaz de Pyrogram

    def tell(self) -> int:
        return self._bytes_recorded

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        """Permite a Pyrogram detectar el tamaño (SEEK_END) o reiniciar (seek 0)."""
        if whence == io.SEEK_END:
            return self.size
        if whence == io.SEEK_SET and offset == 0:
            # Rebobinar el casete implica reiniciar la grabación desde la cinta maestra
            logger.info(f"Rebobinando {self.name}...")
            self._load_new_cassette()
            return 0
        raise io.UnsupportedOperation(
            "Solo se soporta rebobinado (seek 0) o consulta de tamaño (SEEK_END)"
        )
