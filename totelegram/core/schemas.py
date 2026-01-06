from datetime import datetime
from typing import List

from pydantic import BaseModel, Field

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
    created_at: datetime = Field(default_factory=datetime.now)
    strategy: Strategy
    source: SourceMetadata
    parts: List[RemotePart]


class StrategyConfig(BaseModel):
    max_filesize_bytes: int
    upload_limit_rate_kbps: int
    chat_id: str
