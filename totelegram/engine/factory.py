import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import tartape

from totelegram.models import Payload
from totelegram.schemas import SourceType
from totelegram.stream import FileVolume

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
        """Retorna el hash MD5 calculado durante la lectura o manualmente."""
        return getattr(self.stream, "md5sum", "")

    def __enter__(self) -> "UploadStream":
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
    sin que el resto del sistema deba saber si proviene de un archivo simple o de un TAR.
    """

    @staticmethod
    def resolve_naming(payload: Payload, max_filename_len: int = 55) -> Tuple[str, str]:
        """
        Determina el nombre amigable y el caption técnico para Telegram
        en caso de que el nombre exceda el límite permitido por la plataforma.
        """
        if len(payload.filename) <= max_filename_len:
            return payload.filename, ""
        return payload.filename_short, payload.filename

    @classmethod
    def create_stream(
        cls,
        payload: Payload,
        source_path: Path,
        max_filename_len: int = 55,
    ) -> UploadStream:
        """
        Instancia y retorna el UploadStream apropiado según el tipo de recurso.
        """
        filename, caption = cls.resolve_naming(payload, max_filename_len)
        source_type = payload.job.source.type

        logger.debug(
            f"Creando UploadStream para pieza {payload.sequence_index} ({payload.filename}) - Tipo: {source_type}"
        )

        if source_type == SourceType.FOLDER:
            tape = tartape.Tape(source_path)
            raw_volume = tape.get_volume(
                payload.filename,
                payload.sequence_index,
                payload.start_offset,
                payload.end_offset,
            )
        else:
            raw_volume = FileVolume(
                source_path,
                payload.start_offset,
                payload.end_offset,
                payload.filename,
            )

        return UploadStream(
            stream=raw_volume,
            size=payload.size,
            filename=filename,
            caption=caption,
        )
