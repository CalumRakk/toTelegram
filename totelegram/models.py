from datetime import datetime
from pathlib import Path
from typing import Literal, cast, get_args

import peewee
from playhouse.sqlite_ext import JSONField

db = peewee.Proxy()

Category = ["single-file", "pieces-file"]
Status = ["new", "splitted", "uploaded"]


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
    size = peewee.IntegerField()
    md5sum = peewee.CharField(unique=True)
    mimetype = peewee.CharField()
    category = peewee.CharField(
        choices=Category,
        constraints=[peewee.Check("category IN ('single-file', 'pieces-file')")],
    )
    status = peewee.CharField(
        default="new",
        choices=Status,
        constraints=[peewee.Check("status IN ('new', 'splitted', 'uploaded')")],
    )

    @property
    def path(self) -> Path:
        return Path(self.path_str)


class Piece(BaseModel):
    filename = peewee.CharField()
    size = peewee.IntegerField()
    md5sum = peewee.CharField(unique=True)
    status = peewee.CharField(default="new")
    file = peewee.ForeignKeyField(File, backref="pieces")


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
