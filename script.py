import io
import locale
import logging
import os
import re
import time
from pathlib import Path
from typing import List, Union

import peewee

from totelegram.filechunker import FileChunker
from totelegram.models import File, Message, Piece, db
from totelegram.setting import Settings, get_settings
from totelegram.utils import create_md5sum_by_hashlib, get_mimetype

logger = logging.getLogger(__name__)


def _initialize_db(settings: Settings):
    # Inicializar el proxy con la DB real
    database = peewee.SqliteDatabase(str(settings.database_path))
    db.initialize(database)

    database.connect()
    database.create_tables([Piece, Message, File], safe=True)
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


def load_file(path: Path, settings) -> File:
    # TODO: cachar el md5 usando
    md5sum = create_md5sum_by_hashlib(path)
    file = File.get_or_none(md5sum=md5sum)

    if file is not None:
        if file.path_str != str(path):
            file.path_str = str(path)
            file.save()
        return file

    mimetype = get_mimetype(path)
    filesize = path.stat().st_size

    data_file = {
        "path_str": str(path),
        "filename": path.name,
        "size": path.stat().st_size,
        "mimetype": mimetype,
        "md5sum": md5sum,
    }
    # Determina la categoria del File
    if filesize <= settings.max_filesize_bytes:
        data_file["category"] = "single-file"
    else:
        data_file["category"] = "pieces-file"

    file = File.create(**data_file)
    return file


def progress(current: int, total, filename, log_capture_string: io.StringIO):
    regex_capture_seg = re.compile(r"(\d+)\s+seconds")
    logs_capturados = log_capture_string.getvalue()
    if logs_capturados != "":
        log_message = logs_capturados.split("\n")[-2]
        match = regex_capture_seg.search(log_message)
        if match:
            seconds = int(int(match.group(1)) / 2) + 2
            for _ in range(0, seconds):
                logger.info("\t waiting", seconds, "seconds")
                seconds -= 1
                time.sleep(1)
            log_capture_string.truncate(0)
            log_capture_string.seek(0)
        logger.info(f"\t {filename} {current * 100 / total:.1f}%")
    else:
        logger.info(f"\t {filename} {current * 100 / total:.1f}%")


def upload_file(client, settings: Settings, file: File):
    log_capture_string = io.StringIO()

    # Crear un manejador que escriba en el StringIO
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(log_capture_string)],
    )
    # Enviar el archivo al chat
    send_data = {
        "chat_id": settings.chat_id,
        "document": file.path,
    }
    if len(file.path.name) >= settings.max_filename_length:
        # Si el nombre de archivo es grande. Se usa el md5sum como nombre para que Telegram no lo recorte y se agrega un caption para dejar el nombre del archivo. Telegram acepta más caracteres para el caption.
        send_data["file_name"] = f"{file.md5sum}.{file.path.suffix}"
        send_data["caption"] = file.path.name

    messagetelegram = client.send_document(**send_data)

    # Obtener la información del mensaje
    try:
        message_data = {
            "message_id": messagetelegram.id,
            "chat_id": messagetelegram.chat.id,
            "json_data": str(messagetelegram),
            "file": file,
        }
        mesagge = Message.create(**message_data)
        return mesagge
    except Exception as e:
        # TODO: hacer algo con el archivo enviado si ocurre algun error al crear este objeto en la base de datos.
        raise


def upload_piece(client, settings: Settings, piece: Piece):
    log_capture_string = io.StringIO()

    # Crear un manejador que escriba en el StringIO
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(log_capture_string)],
    )
    # Enviar el archivo al chat
    send_data = {
        "chat_id": settings.chat_id,
        "document": piece.path,
    }
    if len(file.path.name) >= settings.max_filename_length:
        # Si el nombre de archivo es grande. Se usa el md5sum como nombre para que Telegram no lo recorte y se agrega un caption para dejar el nombre del archivo. Telegram acepta más caracteres para el caption.
        send_data["file_name"] = f"{piece.file.md5sum}.{file.path.suffix}"
        send_data["caption"] = file.path.name

    messagetelegram = client.send_document(**send_data)

    # Obtener la información del mensaje
    try:
        message_data = {
            "message_id": messagetelegram.id,
            "chat_id": messagetelegram.chat.id,
            "json_data": str(messagetelegram),
            "piece": piece,
        }
        mesagge = Message.create(**message_data)
        return mesagge
    except Exception as e:
        # TODO: hacer algo con el archivo enviado si ocurre algun error al crear este objeto en la base de datos.
        raise


def create_and_save_pieces(chunks: List[Path]):
    with db.atomic():
        pieces = []
        for path in chunks:
            data_piece = {
                "path_str": str(path),
                "filename": path.name,
                "size": path.stat().st_size,
                "file": file,
            }
            piece = Piece.create(**data_piece)
            pieces.append(piece)
    return pieces


def procces_piecesfile_if_is_new(client, settings: Settings, file: File):
    if file.status != "new":
        return
    pieces = FileChunker.split_file(file, settings)
    pieces = create_and_save_pieces(pieces)
    for piece in pieces:
        upload_piece(client, settings, piece)
    file.status = "uploaded"
    file.save()
    return


if __name__ == "__main__":
    settings = get_settings("env/test.env")
    target = Path(
        r"D:\github Leo\toTelegram\tests\Otan Mian Anoixi (Live - Bonus Track)-(240p).mp4"
    )

    _initialize_db(settings)
    client = _initialize_client_telegram(settings)

    paths = target.rglob("*") if target.is_dir() else [target]
    files: List[File] = []
    for path in paths:
        if settings.is_excluded(path):
            continue
        file = load_file(path, settings)
        files.append(file)

    # # Managers
    for file in files:
        if file.status == "uploaded":
            continue

        if file.category == "single-file":
            message = upload_file(client, settings, file)
            file.status = "uploaded"
            file.save()

        else:
            procces_piecesfile_if_is_new(client, settings, file)
            for piece in file.pieces:
                if piece.is_uploaded:
                    continue
                message = upload_piece(client, settings, piece)
                piece.is_uploaded = True
                piece.save()
            file.status = "uploaded"
            file.save()
