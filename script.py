import locale
import os
from pathlib import Path

import peewee

from totelegram.models import File, MessageTelegram, Piece, db
from totelegram.setting import Settings, get_settings
from totelegram.utils import create_md5sum_by_hashlib, get_mimetype


def _initialize_db(settings: Settings):
    # Inicializar el proxy con la DB real
    database = peewee.SqliteDatabase(str(settings.database_path))
    db.initialize(database)

    database.connect()
    database.create_tables([Piece, MessageTelegram, File], safe=True)
    database.close()


def _initialize_client_telegram(settings: Settings):
    from pyrogram.client import Client

    lang, encoding = locale.getlocale()
    iso639 = "en"
    if lang:
        iso639 = lang.split("_")[0]

    client = Client(
        settings.session_name,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        workdir=str(settings.worktable),
        lang_code=iso639,
    )
    client.start()  # type: ignore
    # telegram.check_session()
    # telegram.check_chat_id()
    return client


def load_file(path: Path, settings):
    # TODO: cachar el md5 usando
    md5sum = create_md5sum_by_hashlib(path)
    mimetype = get_mimetype(path)

    filesize = path.stat().st_size

    if filesize <= settings.max_filesize_bytes:
        file = File(
            path=str(path),
            filename=path.name,
            size=path.stat().st_size,
            mimetype=mimetype,
            md5sum=md5sum,
            category="single-file",
        )
    file.save()
    return file


if __name__ == "__main__":
    settings = get_settings()
    target = Path(
        r"D:\github Leo\toTelegram\tests\Otan Mian Anoixi (Live - Bonus Track)-(240p).mp4"
    )

    _initialize_db(settings)
    # client = _initialize_client_telegram(settings)

    paths = target.rglob("*") if target.is_dir() else [target]
    files = []
    for path in paths:
        if settings.is_excluded(path):
            continue
        file = load_file(path, settings)
        files.append(file)

    # # Managers
    # for file in files:
    #     if file.type == "pieces-file":
    #         if file.status == "unfinished":
    #             pieces = split_file(file, setting)
    #             file.status = "splitted"
    #             file.pieces = pieces
    #             file.save()

    #         for piece in file.pieces:
    #             if piece.is_uploaded:
    #                 continue
    #             message = upload_piece(piece, telegram=telegram)
    #             piece.message = message
    #             piece.is_uploaded = True
    #             piece.save()
    #         file.status = "finished"
    #         file.save()
    #     else:
    #         if file.status == "uploaded":
    #             continue
    #         message = upload_file(file, telegram=telegram)
    #         file.message = message
    #         file.status = "uploaded"
    #         file.save()
