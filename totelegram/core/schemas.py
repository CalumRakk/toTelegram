from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from totelegram import __version__
from totelegram.core.enums import Strategy

MANIFEST_VERSION = "4.0"


class SourceMetadata(BaseModel):
    filename: str
    size: int
    md5sum: str
    mime_type: str
    mtime: float


class RemotePart(BaseModel):
    sequence: int
    message_id: int
    chat_id: int
    link: str
    part_filename: str
    part_size: int
    part_md5sum: str


class UploadManifest(BaseModel):
    version: str = MANIFEST_VERSION
    app_version: str
    created_at: datetime

    # Bloque de Identidad del Job
    strategy: Strategy
    chunk_size: int  # El 'tg_max_size' del contrato original (ADR-002)

    # Bloque de Identidad del Chat/Usuario
    target_chat_id: int
    owner_id: int
    owner_name: str

    source: SourceMetadata
    parts: List[RemotePart]


class StrategyConfig(BaseModel):
    # "Contrato de Partición".
    tg_max_size: int

    # Nos dice si el Job nació bajo el privilegio de una cuenta Premium.
    user_is_premium: bool

    app_version: str


class ProfileRegistry(BaseModel):
    """Modelo que representa el archivo config.json global de perfiles"""

    active: Optional[str] = None
    profiles: Dict[str, str] = Field(default_factory=dict)


class Inventory(BaseModel):
    fingerprint: str
    total_size: int
    total_files: int
    scan_date: float
    scan_version: str
    db_path: str
