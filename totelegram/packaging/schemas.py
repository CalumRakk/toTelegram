from datetime import datetime
from pathlib import Path

from pydantic import AliasChoices, BaseModel, Field

from totelegram import __VERSION__
from totelegram.schemas import SourceType, Strategy, TapeCatalog

# Versión del esquema del manifiesto
MANIFEST_VERSION = "6.0"

# Constantes del contenedor híbrido para cintas de datos (.ttape)
TTAPE_EXTENSION = ".ttape"
TTAPE_MANIFEST_FILENAME = "manifest.json"
TTAPE_CATALOG_FILENAME = "catalog.tartape"


class FileFragment(BaseModel):
    """GPS exacto de un archivo dentro de un volumen (Payload)."""

    vol_idx: int  # sequence_index del Payload
    volume_offset: int = Field(
        ...,
        validation_alias=AliasChoices("volume_offset", "offset_in_vol"),
        description="Offset de inicio dentro del archivo .tar del volumen",
    )
    volume_length: int = Field(
        ...,
        validation_alias=AliasChoices("volume_length", "bytes_in_volume"),
        description="Cantidad de bytes útiles en este volumen",
    )
    source_offset: int = Field(
        default=0,
        description="Offset de inicio dentro del archivo original reconstruido",
    )

    @property
    def offset_in_vol(self) -> int:
        return self.volume_offset

    @property
    def bytes_in_volume(self) -> int:
        return self.volume_length


class TapeMemberSnapshot(BaseModel):
    """Representación de un archivo dentro de una carpeta archivada (utilizado en v5.x / migración)."""

    relative_path: str
    size: int
    md5sum: str
    start_offset: int = Field(
        default=0, description="Offset de inicio en el TAR continuo"
    )
    fragments: list[FileFragment] = Field(default_factory=list)


class SourceMetadata(BaseModel):
    filename: str
    size: int
    md5sum: str
    mime_type: str
    mtime: float
    type: SourceType
    tape_catalog: TapeCatalog | None = None
    # En v6.0 (.ttape), inventory es None porque el inventario reside en catalog.tartape
    inventory: list[TapeMemberSnapshot] | None = None


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
    parts: list[RemotePart]


class TapeContainerInfo(BaseModel):
    """DTO de inspección para contenedores híbridos .ttape."""

    path: Path
    manifest: UploadManifest
    manifest_size_bytes: int
    catalog_compressed_bytes: int
    catalog_uncompressed_bytes: int
    total_container_bytes: int


# Modelos virtuales para particionado en memoria
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


class VirtualFileFragment(BaseModel):
    vol_idx: int
    volume_offset: int
    volume_length: int
    source_offset: int = 0
    state: str

    @property
    def offset_in_vol(self) -> int:
        return self.volume_offset

    @property
    def bytes_in_volume(self) -> int:
        return self.volume_length


class VirtualTapeMember(BaseModel):
    relative_path: str
    size: int
    md5sum: str
    start_offset: int = 0
    fragments: list[VirtualFileFragment] = Field(default_factory=list)


class VirtualTapeCatalog(BaseModel):
    fingerprint: str
    total_size: int
    total_files: int
    tartape_version: str
    created_at: float
    exclude_patterns: str
