import logging
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from totelegram import __VERSION__
from totelegram.schemas import SourceType, Strategy, TapeCatalog

logger = logging.getLogger(__name__)

MANIFEST_VERSION = "5.0"


class FileFragment(BaseModel):
    """GPS exacto de un archivo dentro de un volumen (Payload)."""

    vol_idx: int  # sequence_index del Payload
    offset_in_vol: int  # Offset de inicio dentro del archivo .tar del volumen
    bytes_in_volume: (
        int  # Cantidad de bytes (o donde termina) este archivo en este volumen
    )


class TapeMemberSnapshot(BaseModel):
    """Representación de un archivo dentro de una carpeta archivada."""

    relative_path: str
    size: int
    md5sum: str
    fragments: List[FileFragment]


class SourceMetadata(BaseModel):
    filename: str
    size: int
    md5sum: str
    mime_type: str
    mtime: float
    type: SourceType
    tape_catalog: Optional[TapeCatalog] = None
    inventory: Optional[List[TapeMemberSnapshot]] = None


class RemotePart(BaseModel):
    sequence: int
    message_id: int
    chat_id: int
    link: str
    part_filename: str
    part_size: int
    part_md5sum: str
    start_offset: int  # Offset global en el Source (virtualización)
    end_offset: int  # Offset global en el Source (virtualización)


class UploadManifest(BaseModel):
    manifest_version: str = MANIFEST_VERSION
    app_version: str = __VERSION__
    created_at: datetime
    strategy: Strategy
    chunk_size: int
    chat_id: int
    owner_id: int
    owner_name: str
    source: SourceMetadata
    parts: List[RemotePart]


class VirtualChunk(BaseModel):
    """
    Representación lógica y pura de un fragmento o volumen físico.
    Esto se convertirá posteriormente en un registro de la tabla 'Payload'.
    """

    sequence_index: int = Field(
        ..., description="Índice secuencial de la pieza (0, 1, 2...)"
    )
    start_offset: int = Field(
        ..., description="Offset de byte inicial en el recurso de origen"
    )
    end_offset: int = Field(
        ..., description="Offset de byte final (exclusivo) en el recurso de origen"
    )
    size: int = Field(..., description="Tamaño total en bytes de esta partición")
    filename: str = Field(..., description="Nombre amigable propuesto para Telegram")
    filename_short: str = Field(
        ..., description="Nombre corto o hash propuesto para evadir límites de longitud"
    )
    md5sum: Optional[str] = Field(
        default=None, description="MD5 del volumen físico (calculado durante la subida)"
    )


class VirtualFileFragment(BaseModel):
    """
    GPS de un fragmento de archivo interno dentro de una cinta.
    Mapea cómo se distribuyen los bytes de un archivo dentro de los volúmenes virtuales.
    """

    vol_idx: int = Field(
        ..., description="Índice del volumen (VirtualChunk) donde reside este fragmento"
    )
    offset_in_vol: int = Field(
        ..., description="Offset de inicio dentro del volumen .tar"
    )
    bytes_in_volume: int = Field(
        ..., description="Cantidad de bytes del archivo en este volumen"
    )
    state: str = Field(
        ...,
        description="Estado de integridad del fragmento según tartape (ej: COMPLETED)",
    )


class VirtualTapeMember(BaseModel):
    """
    Representación lógica de un archivo individual contenido dentro de una carpeta archivada.
    Esto se convertirá posteriormente en registros de 'TapeMember' y 'TapeMemberGPS'.
    """

    relative_path: str = Field(
        ..., description="Ruta relativa del archivo dentro de la carpeta de origen"
    )
    size: int = Field(..., description="Tamaño real en bytes del archivo desarchivado")
    md5sum: str = Field(..., description="Firma MD5 única del archivo")
    fragments: List[VirtualFileFragment] = Field(
        default_factory=list,
        description="Lista de fragmentos lógicos que componen el archivo físico",
    )


class VirtualTapeCatalog(BaseModel):
    """
    Metadatos globales de la cinta generada para una carpeta.
    Esto se almacenará en la columna JSON de la tabla 'Source'.
    """

    fingerprint: str = Field(
        ..., description="Firma única del estado actual del directorio"
    )
    total_size: int = Field(
        ..., description="Suma de los tamaños de todos los archivos contenidos"
    )
    total_files: int = Field(
        ..., description="Cantidad de archivos indexados en la cinta"
    )
    tartape_version: str = Field(
        ..., description="Versión de tartape utilizada para generar la estructura"
    )
    created_at: float = Field(..., description="Timestamp Unix de creación del índice")
    exclude_patterns: str = Field(
        ..., description="Lista de exclusión serializada aplicada durante el escaneo"
    )
