import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Self, Tuple

import tartape

from totelegram.engine.events import TarUploadObserverBridge, UploadObserver

# from tartape.stream import FileSliceStream
from totelegram.models import Payload
from totelegram.schemas import SourceType
from totelegram.stream import FileVolume
from totelegram.telegram.captions import ChunkCaption

logger = logging.getLogger(__name__)


@dataclass
class UploadStream:
    """
    Contenedor unificado para un flujo binario listo para transmitir a Telegram.
    Funciona como Context Manager y provee metadatos esenciales para Pyrogram.
    """

    stream: io.BufferedIOBase
    size: int
    filename: str
    caption: str

    @property
    def md5sum(self) -> str:
        """Retorna el hash calculado en vuelo durante la lectura, o computado como fallback."""
        val = getattr(self.stream, "checksum", None) or getattr(
            self.stream, "md5sum", None
        )
        # Salvaguarda: Si Pyrogram hizo un seek no lineal (reintento), computar bajo demanda
        if not val and hasattr(self.stream, "compute_checksum"):
            try:
                # FIXME: io.BUfferedIOBase no implementa compute_checksum
                val = self.stream.compute_checksum(algorithm="md5")  # type: ignore
            except Exception as e:
                logger.warning(
                    f"No se pudo computar checksum de respaldo para {self.filename}: {e}"
                )
        return val or ""

    def __enter__(self) -> Self:
        if hasattr(self.stream, "__enter__"):
            self.stream.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self.stream, "__exit__"):
            return self.stream.__exit__(exc_type, exc_val, exc_tb)


class PayloadStreamFactory:
    """
    Fábrica transparente de flujos de datos.
    Traduce una pieza lógica (Payload en DB) a un flujo binario físico listo para subir,
    utilizando el motor de streaming nativo de TarTape 3.0.
    """

    @staticmethod
    def resolve_naming(payload: Payload, max_filename_len: int = 55) -> Tuple[str, str]:
        """Determina el nombre amigable para el archivo y el caption estructurado para Telegram."""
        if len(payload.filename) <= max_filename_len:
            filename = payload.filename
        else:
            filename = payload.filename_short

        total_parts = payload.job.payloads.count()
        master_short_hash = payload.job.source.short_hash

        caption = ChunkCaption(
            filename=payload.filename,
            part_number=payload.sequence_index + 1,
            total_parts=total_parts,
            part_size=payload.size,
            source_short_hash=master_short_hash,
        ).render()

        return filename, caption

    @classmethod
    def create_stream(
        cls,
        payload: Payload,
        source_path: Path,
        max_filename_len: int = 55,
        observer: UploadObserver | None = None,
    ) -> UploadStream:
        filename, caption = cls.resolve_naming(payload, max_filename_len)
        source_type = payload.job.source.type

        logger.debug(
            f"Creando UploadStream para pieza {payload.sequence_index} ({payload.filename}) - Tipo: {source_type}"
        )

        if source_type == SourceType.FOLDER:
            tape = tartape.Tape(source_path)
            # Conectamos el puente de telemetría si se proporcionó un observer
            tar_observer = TarUploadObserverBridge(observer) if observer else None

            raw_volume = tape.get_volume(
                index=payload.sequence_index,
                size=payload.job.config.tg_max_size,
                observer=tar_observer,
            )
        else:
            raw_volume = FileVolume(
                path=source_path,
                start_offset=payload.start_offset,
                end_offset=payload.end_offset,
                name=payload.filename,
            )

        return UploadStream(
            stream=raw_volume,
            size=payload.size,
            filename=filename,
            caption=caption,
        )
