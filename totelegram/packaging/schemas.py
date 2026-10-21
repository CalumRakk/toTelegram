from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from totelegram import __VERSION__
from totelegram.schemas import SourceType, Strategy, TapeCatalog

# Versión canónica del esquema del manifiesto en la rama principal
MANIFEST_VERSION = "6.0"

# Constantes del contenedor híbrido para cintas de datos (.ttape)
TTAPE_EXTENSION = ".ttape"
TTAPE_MANIFEST_FILENAME = "manifest.json"
TTAPE_CATALOG_FILENAME = "catalog.tartape"


class SourceMetadata(BaseModel):
    """Metadatos del recurso original (archivo o carpeta)."""

    filename: str
    size: int
    md5sum: str
    mime_type: str
    mtime: float
    type: SourceType
    tape_catalog: TapeCatalog | None = None


class RemotePart(BaseModel):
    """Metadatos de cada volumen o trozo subido físicamente a Telegram."""

    sequence: int
    message_id: int
    chat_id: int
    link: str
    part_filename: str
    part_size: int
    part_md5sum: str
    start_offset: int  # Offset global en el Source continuo
    end_offset: int  # Offset global en el Source continuo


class UploadManifest(BaseModel):
    """Manifiesto de navegación y disponibilidad v6.0."""

    manifest_version: str = MANIFEST_VERSION
    app_version: str = __VERSION__
    created_at: datetime
    strategy: Strategy
    chunk_size: int
    chat_id: int
    owner_id: int
    owner_name: str
    source: SourceMetadata
    parts: list[RemotePart]


class TapeContainerInfo(BaseModel):
    """DTO de inspección técnica para contenedores híbridos .ttape."""

    path: Path
    manifest: UploadManifest
    manifest_size_bytes: int
    catalog_compressed_bytes: int
    catalog_uncompressed_bytes: int
    total_container_bytes: int


# Modelos virtuales para el particionado en memoria
class VirtualChunk(BaseModel):
    sequence_index: int = Field(
        ..., description="Índice secuencial de la pieza (0, 1, 2...)"
    )
    start_offset: int = Field(
        ..., description="Offset de byte inicial en el recurso de origen"
    )
    end_offset: int = Field(
        ..., description="Offset de byte final en el recurso de origen"
    )
    size: int = Field(..., description="Tamaño total en bytes de esta partición")
    filename: str = Field(..., description="Nombre amigable propuesto para Telegram")
    filename_short: str = Field(..., description="Nombre corto o hash propuesto")
    md5sum: str | None = Field(default=None, description="MD5 del volumen físico")


class VirtualTapeMember(BaseModel):
    """Representación ligera de un archivo de la carpeta durante la indexación inicial."""

    relative_path: str
    size: int
    md5sum: str
    start_offset: int = 0


class VirtualTapeCatalog(BaseModel):
    fingerprint: str
    total_size: int
    total_files: int
    tartape_version: str
    created_at: float
    exclude_patterns: str
