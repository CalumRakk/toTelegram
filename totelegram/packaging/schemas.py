from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from totelegram import __VERSION__
from totelegram.schemas import SourceType, TapeCatalog

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
    chunk_size: int
    chat_id: int
    owner_id: int
    owner_name: str
    source: SourceMetadata
    parts: list[RemotePart]

    @property
    def is_chunked(self) -> bool:
        """Determina si el recurso físico fue dividido en múltiples partes."""
        return len(self.parts) > 1


class TapeContainerInfo(BaseModel):
    """DTO de inspección técnica para contenedores híbridos .ttape."""

    path: Path
    manifest: UploadManifest
    manifest_size_bytes: int
    catalog_compressed_bytes: int
    catalog_uncompressed_bytes: int
    total_container_bytes: int


@dataclass(slots=True)
class VirtualChunk:
    """Representación ligera en memoria de una partición lógica."""

    sequence_index: int
    start_offset: int
    end_offset: int
    size: int
    filename: str
    filename_short: str
    md5sum: str | None = None
