import enum
from datetime import datetime
from pathlib import Path
from typing import Literal, cast, get_args

import peewee
from playhouse.sqlite_ext import JSONField

db = peewee.Proxy()


class FileCategory(str, enum.Enum):
    SINGLE = "single-file"
    CHUNKED = "pieces-file"


class FileStatus(str, enum.Enum):
    NEW = "NEW"
    SPLITTED = "SPLITTED"
    UPLOADED = "UPLOADED"


class BaseModel(peewee.Model):
    created_at = peewee.DateTimeField(default=datetime.now)
    updated_at = peewee.DateTimeField(default=datetime.now)

    def save(self, *args, **kwargs):
        self.updated_at = datetime.now()
        return super().save(*args, **kwargs)

    class Meta:
        database = db


class File(BaseModel):
    path_str = cast(str, peewee.CharField())

    filename = peewee.CharField()
    size = cast(int, peewee.IntegerField())
    md5sum = cast(str, peewee.CharField(unique=True))
    mimetype = peewee.CharField()
    category = peewee.CharField(
        constraints=[peewee.Check("category IN ('single-file', 'pieces-file')")],
    )
    status = cast(
        str,
        peewee.CharField(
            default="NEW",
            constraints=[peewee.Check("status IN ('NEW', 'SPLITTED', 'UPLOADED')")],
        ),
    )

    def get_status(self) -> FileStatus:
        return FileStatus(self.status)

    @property
    def path(self) -> Path:
        return Path(self.path_str)

    @property
    def pieces(self) -> list["Piece"]:
        # sobreescribe la busqueda inverta de peewee para evitar el falto positivo de pylance
        return Piece.select().where(Piece.file == self)

    @property
    def type(self) -> FileCategory:
        return FileCategory(self.category)


class Piece(BaseModel):
    path_str = cast(str, peewee.CharField())
    filename = peewee.CharField()
    size = peewee.IntegerField()
    is_uploaded = cast(bool, peewee.BooleanField(default=False))
    file = peewee.ForeignKeyField(File, backref="pieces")

    @property
    def path(self) -> Path:
        return Path(self.path_str)


class Message(BaseModel):
    message_id = peewee.IntegerField()
    chat_id = peewee.IntegerField()
    json_data = JSONField()
    file = peewee.ForeignKeyField(File, backref="message", null=True)
    piece = peewee.ForeignKeyField(Piece, backref="messages", null=True)

    def save(self, *args, **kwargs):
        if (self.file is None and self.piece is None) or (
            self.file is not None and self.piece is not None
        ):
            raise ValueError(
                "MessageTelegram debe estar relacionado con un File O un Piece, no ambos."
            )
        return super().save(*args, **kwargs)
