from datetime import datetime

import peewee
from playhouse.sqlite_ext import JSONField

db = peewee.Proxy()


class BaseModel(peewee.Model):
    created_at = peewee.DateTimeField(default=datetime.now)
    updated_at = peewee.DateTimeField(default=datetime.now)

    def save(self, *args, **kwargs):
        self.updated_at = datetime.now()
        return super().save(*args, **kwargs)

    class Meta:
        database = db


class File(BaseModel):
    filename = peewee.CharField()
    size = peewee.IntegerField()
    md5sum = peewee.CharField(unique=True)
    mimetype = peewee.CharField()
    category = peewee.CharField()
    status = peewee.CharField(default="new")


class Piece(BaseModel):
    filename = peewee.CharField()
    size = peewee.IntegerField()
    md5sum = peewee.CharField(unique=True)
    status = peewee.CharField(default="new")
    file = peewee.ForeignKeyField(File, backref="pieces")


class MessageTelegram(BaseModel):
    message_id = peewee.IntegerField()
    chat_id = peewee.IntegerField()
    json_data = JSONField()
    file = peewee.ForeignKeyField(File, backref="messages", null=True)
    piece = peewee.ForeignKeyField(Piece, backref="messages", null=True)
