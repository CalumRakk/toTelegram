from typing import Literal

from pydantic import BaseModel
from typing_extensions import TypedDict


class File(BaseModel):
    kind: Literal["file"]
    filename: str
    fileExtension: str
    mimeType: str
    md5sum: str
    size: int
    medatada: dict


class Message(BaseModel):
    file_name: str
    message_id: int
    size: int
    chat_id: int
    link: str


class Piece(BaseModel):
    kind: Literal["#piece"]
    filename: str
    size: int
    message: Message


class ManagerPieces(BaseModel):
    kind: Literal["pieces-file"]
    file: File
    pieces: dict


class ManagerSingleFile(BaseModel):
    kind: Literal["single-file"]
    file: File
    message: Message


class Snapshot(BaseModel):
    kind: Literal["single-file", "pieces-file"]
    manager: ManagerSingleFile | ManagerPieces
    createdTime: str  # comprobar el formato de datetime
    version :str = "3.0"
