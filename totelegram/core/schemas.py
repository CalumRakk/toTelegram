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


class RemotePart(BaseModel):
    sequence: int = Field(description="Orden de la parte, iniciando en 0")
    message_id: int
    chat_id: int
    link: str
    part_filename: str
    part_size: int


class UploadManifest(BaseModel):
    version: str = MANIFEST_VERSION
    app_version: str = __version__
    created_at: datetime = Field(default_factory=datetime.now)
    strategy: Strategy
    source: SourceMetadata
    parts: List[RemotePart]


class StrategyConfig(BaseModel):
    tg_max_size: int
    chat_id: str | int
    user_id: int
    app_version: str


class ProfileRegistry(BaseModel):
    """Modelo que representa el archivo config.json global de perfiles"""

    active: Optional[str] = None
    profiles: Dict[str, str] = Field(default_factory=dict)
