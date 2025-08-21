import io
import json
import locale
import logging
from pathlib import Path
from typing import List

import peewee

from totelegram.filechunker import FileChunker
from totelegram.logging_config import setup_logging
from totelegram.models import File, FileCategory, FileStatus, Message, Piece, db
from totelegram.setting import Settings, get_settings
from totelegram.utils import create_md5sum_by_hashlib, get_mimetype

logger = logging.getLogger(__name__)


def init_database(settings: Settings):
    logger.info("Iniciando base de datos en %s", settings.database_path)
    # Inicializar el proxy con la DB real
    database = peewee.SqliteDatabase(str(settings.database_path))
    db.initialize(database)

    database.connect()
    database.create_tables([Piece, Message, File], safe=True)
    logger.info("Base de datos inicializada correctamente")
    database.close()


def init_telegram_client(settings: Settings):
    logger.info("Iniciando cliente de Telegram")

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
    logger.info("Cliente de Telegram inicializado correctamente")
    # telegram.check_session()
    # telegram.check_chat_id()
    return client


def _get_or_create_file_record(path: Path, settings: Settings) -> File:
    logger.debug(f"Analizando archivo: {path.name}")
    md5sum = create_md5sum_by_hashlib(path)
    file = File.get_or_none(md5sum=md5sum)

    if file is not None:
        logger.debug(f"El archivo {path.name} ya se encuentra en la base de datos")
        if file.path_str != str(path):
            file.path_str = str(path)
            file.save()
        return file
    logger.debug(f"El archivo {path.name} no se encuentra en la base de datos")
    mimetype = get_mimetype(path)
    filesize = path.stat().st_size

    file_data = {
        "path_str": str(path),
        "filename": path.name,
        "size": path.stat().st_size,
        "mimetype": mimetype,
        "md5sum": md5sum,
    }
    # Determina la categoria del File
    if filesize <= settings.max_filesize_bytes:
        file_data["category"] = FileCategory.SINGLE.value
        logger.debug(f"El archivo {path.name} es un archivo single-file")
    else:
        file_data["category"] = FileCategory.CHUNKED.value
        logger.debug(f"El archivo {path.name} es un archivo pieces-file")

    file = File.create(**file_data)
    logger.debug(f"El archivo {path.name} ha sido creado en la base de datos")
    return file


# def _track_upload_progress(
#     current: int, total, filename, log_capture_string: io.StringIO
# ):
#     seconds_regex = re.compile(r"(\d+)\s+seconds")
#     captured_logs = log_capture_string.getvalue()
#     if captured_logs != "":
#         log_message = captured_logs.split("\n")[-2]
#         match = seconds_regex.search(log_message)
#         if match:
#             seconds = int(int(match.group(1)) / 2) + 2
#             for _ in range(0, seconds):
#                 logger.info(f"waiting {seconds} seconds...")
#                 seconds -= 1
#                 time.sleep(1)
#             log_capture_string.truncate(0)
#             log_capture_string.seek(0)
#         logger.info(f"\t {filename} {current * 100 / total:.1f}%")
#     else:
#         logger.info(f"\t {filename} {current * 100 / total:.1f}%")


def upload_single_file(client, settings: Settings, file: File):
    logger.info(f"Subiendo {FileCategory.SINGLE.value} {file.path.name} a Telegram...")

    # # Crear un manejador que escriba en el StringIO
    # log_capture_string = io.StringIO()
    # logging.basicConfig(
    #     level=logging.WARNING,
    #     format="%(asctime)s - %(levelname)s - %(message)s",
    #     handlers=[logging.StreamHandler(log_capture_string)],
    # )

    # Enviar el archivo al chat
    send_data = {
        "chat_id": settings.chat_id,
        "document": file.path,
    }
    if len(file.path.name) >= settings.max_filename_length:
        # Si el nombre de archivo es grande. Se usa el md5sum como nombre para que Telegram no lo recorte y se agrega un caption para dejar el nombre del archivo. Telegram acepta más caracteres para el caption.
        send_data["file_name"] = f"{file.md5sum}.{file.path.suffix}"
        send_data["caption"] = file.path.name

    message_telegram = client.send_document(**send_data)
    logger.info(
        f"Subiendo {FileCategory.SINGLE.value} {file.path.name} a Telegram... OK"
    )

    # Obtener la información del mensaje
    try:
        message_data = {
            "message_id": message_telegram.id,
            "chat_id": message_telegram.chat.id,
            "json_data": str(message_telegram),
            "file": file,
        }
        message = Message.create(**message_data)
        return message
    except Exception as e:
        # TODO: hacer algo con el archivo enviado si ocurre algun error al crear este objeto en la base de datos.
        raise


def upload_piece_to_telegram(client, settings: Settings, piece: Piece):
    logger.info(f"Analizando Piece {piece.filename}...")
    if piece.is_uploaded:
        logger.info(f"Piece {piece.filename} ya fue subido a telegram.")
        return
    logger.info(f"Subiendo Piece {piece.filename} a Telegram...")

    # # Crear un manejador que escriba en el StringIO
    # log_capture_string = io.StringIO()
    # logging.basicConfig(
    #     level=logging.WARNING,
    #     format="%(asctime)s - %(levelname)s - %(message)s",
    #     handlers=[logging.StreamHandler(log_capture_string)],
    # )

    # Enviar el archivo al chat
    send_data = {
        "chat_id": settings.chat_id,
        "document": piece.path,
    }
    if len(piece.file.path.name) >= settings.max_filename_length:
        # Si el nombre de archivo es grande. Se usa el md5sum como nombre para que Telegram no lo recorte y se agrega un caption para dejar el nombre del archivo. Telegram acepta más caracteres para el caption.
        send_data["file_name"] = f"{piece.file.md5sum}.{piece.file.path.suffix}"
        send_data["caption"] = piece.file.path.name

    message_telegram = client.send_document(**send_data)
    logger.info(f"Subiendo Piece {piece.filename} a Telegram... OK")

    # Obtener la información del mensaje
    try:
        message = Message.create(
            message_id=message_telegram.id,
            chat_id=message_telegram.chat.id,
            json_data=json.loads(str(message_telegram)),
            piece=piece,
        )
        return message
    except Exception as e:
        # TODO: hacer algo con el archivo enviado si ocurre algun error al crear este objeto en la base de datos.
        raise


def save_pieces(chunks: List[Path], file: File) -> List[Piece]:
    logger.info(f"Guardando {len(chunks)} pieces...")
    with db.atomic():
        pieces = []
        for path in chunks:
            piece = Piece.create(
                path_str=str(path),
                filename=path.name,
                size=path.stat().st_size,
                file=file,
            )
            pieces.append(piece)
    logger.info(f"Guardando {len(chunks)} pieces... OK")
    return pieces


def _get_or_chunked_file(file: File, settings: Settings) -> List[Piece]:
    if file.type != FileCategory.CHUNKED:
        raise Exception(
            f"File category '{file.type.value}' no es '{FileCategory.CHUNKED.value}'"
        )

    if file.get_status() == FileStatus.NEW:
        logger.info(f"Dividiendo archivo: {file.path.name}...")
        chunks = FileChunker.split_file(file, settings)
        logger.info(f"Dividiendo archivo: {file.path.name}... OK")
        pieces = save_pieces(chunks, file)
        file.status = FileStatus.SPLITTED.value
        file.save()
        return pieces
    elif file.get_status() == FileStatus.SPLITTED:
        return file.pieces
    raise Exception("File status no es 'new' o 'splitted'")


def handle_pieces_file(client, settings: Settings, file: File):
    logger.info(f"Analizando archivo: {file.path.name} {file.type.value}")

    if file.get_status() == FileStatus.UPLOADED:
        logger.info(f"Archivo {file.path.name} ya fue subido a telegram.")
        return

    pieces = _get_or_chunked_file(file, settings)
    for piece in pieces:
        upload_piece_to_telegram(client, settings, piece)

    file.status = FileStatus.UPLOADED.value
    file.save()
    return


def get_or_create_file_records(paths: List[Path], settings: Settings) -> List[File]:
    logger.info(f"Se encontraron {len(paths)} archivos para procesar")
    file_records = []
    for path in paths:
        if not path.exists():
            continue
        elif path.is_dir():
            continue
        elif settings.is_excluded(path):
            continue

        file = _get_or_create_file_record(path, settings)
        file_records.append(file)
    return file_records


def main():
    setup_logging(f"{__file__}.log", logging.DEBUG)

    settings = get_settings("env/test.env")
    target = Path(
        r"D:\github Leo\toTelegram\tests\Otan Mian Anoixi (Live - Bonus Track)-(240p).mp4"
    )

    init_database(settings)

    paths = list(target.glob("*")) if target.is_dir() else [target]
    file_records = get_or_create_file_records(paths, settings)

    # # Managers
    client = None
    for file in file_records:
        if file.get_status() == FileStatus.UPLOADED:
            continue

        if client is None:
            client = init_telegram_client(settings)

        if file.type == FileCategory.SINGLE:
            upload_single_file(client, settings, file)
            file.status = FileStatus.UPLOADED.value
            file.save()
        else:
            handle_pieces_file(client, settings, file)


if __name__ == "__main__":
    main()
