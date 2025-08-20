import os
from pathlib import Path
from typing import Union

import peewee

from toTelegram.config import Config
from toTelegram.exclusionManager import ExclusionManager
from toTelegram.managers import PiecesFile, SingleFile
from totelegram.models import File, MessageTelegram, Piece, db
from totelegram.setting import Settings
from toTelegram.telegram import Telegram
from toTelegram.types.file import File


def toggle_config_path():
    file = Path(r"session.txt")
    content = file.read_text().strip().lower()
    if content == "leo":
        config_path = r"config - leo.yaml"
        file.write_text("tek")
        return config_path
    else:
        config_path = r"config - tek.yaml"
        file.write_text("leo")
        return config_path


# if __name__ == "__main__":
#     settings = Settings()
#     target = Path("F:\SUBIR TIKTOK")

#     # Si el usuario no especifica nada, usa un default
#     database_path = settings.database_path or Path("/tmp/default.sqlite")

#     # Inicializar el proxy con la DB real
#     database = peewee.SqliteDatabase(database_path)
#     db.initialize(database)

#     database.connect()
#     database.create_tables([File, Piece, MessageTelegram], safe=True)
#     database.close()

#     telegram = Telegram(config=target)
#     telegram.check_session()
#     telegram.check_chat_id()

#     paths = target.rglob("*") if target.is_dir() else [target]
#     files = []
#     for path in paths:
#         if setting.is_excluded(path):
#             continue
#         file = File.from_path(path)
#         files.append(Path(target / file))

#     # Managers
#     for file in files:
#         if file.type == "pieces-file":
#             if file.status == "unfinished":
#                 pieces = split_file(file, setting)
#                 file.status = "splitted"
#                 file.pieces = pieces
#                 file.save()

#             for piece in file.pieces:
#                 if piece.is_uploaded:
#                     continue
#                 message = upload_piece(piece, telegram=telegram)
#                 piece.message = message
#                 piece.is_uploaded = True
#                 piece.save()
#             file.status = "finished"
#             file.save()
#         else:
#             if file.status == "uploaded":
#                 continue
#             message = upload_file(file, telegram=telegram)
#             file.message = message
#             file.status = "uploaded"
#             file.save()
