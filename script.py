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
    logger.info(f"Iniciando base de datos en {settings.database_path}")
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
    return client


def _get_or_create_file_record(path: Path, settings: Settings) -> File:
    logger.debug(f"Analizando archivo: {path.name}")
    md5sum = create_md5sum_by_hashlib(path)
    file = File.get_or_none(md5sum=md5sum)

    if file is not None:
        logger.debug(f"El archivo {path.name} ya está en la base de datos")
        if file.path_str != str(path):
            logger.debug(f"Actualizando ruta de {path.name} en base de datos")
            file.path_str = str(path)
            file.save()
        return file
    logger.debug(f"El archivo {path.name} no está en la base de datos")
    mimetype = get_mimetype(path)
    filesize = path.stat().st_size

    file_data = {
        "path_str": str(path),
        "filename": path.name,
        "size": path.stat().st_size,
        "mimetype": mimetype,
        "md5sum": md5sum,
    }
    if filesize <= settings.max_filesize_bytes:
        file_data["category"] = FileCategory.SINGLE.value
        logger.debug(f"El archivo {path.name} es de tipo SINGLE")
    else:
        file_data["category"] = FileCategory.CHUNKED.value
        logger.debug(f"El archivo {path.name} es de tipo CHUNKED")

    file = File.create(**file_data)
    logger.debug(f"El archivo {path.name} fue creado en la base de datos")
    return file


def upload_single_file(client, settings: Settings, file: File):
    logger.info(f"Subiendo archivo único: {file.path.name}…")

    send_data = {
        "chat_id": settings.chat_id,
        "document": file.path,
    }
    if len(file.path.name) >= settings.max_filename_length:
        logger.debug(f"El nombre de {file.path.name} excede el límite, se usará md5sum")
        send_data["file_name"] = f"{file.md5sum}.{file.path.suffix}"
        send_data["caption"] = file.path.name

    message_telegram = client.send_document(**send_data)
    logger.info(f"Archivo único subido correctamente: {file.path.name}")

    try:
        message_data = {
            "message_id": message_telegram.id,
            "chat_id": message_telegram.chat.id,
            "json_data": str(message_telegram),
            "file": file,
        }
        message = Message.create(**message_data)
        logger.debug(
            f"Registro de mensaje creado en base de datos para {file.path.name}"
        )
        return message
    except Exception as e:
        logger.error(f"Error al registrar mensaje de {file.path.name}: {e}")
        raise


def upload_piece_to_telegram(client, settings: Settings, piece: Piece):
    if piece.is_uploaded:
        logger.info(f"La pieza {piece.filename} ya fue subida a Telegram")
        return

    logger.info(f"Subiendo pieza: {piece.filename}…")
    send_data = {
        "chat_id": settings.chat_id,
        "document": piece.path,
    }
    if len(piece.file.path.name) >= settings.max_filename_length:
        logger.debug(
            f"El nombre de {piece.file.path.name} excede el límite, se usará md5sum"
        )
        send_data["file_name"] = f"{piece.file.md5sum}.{piece.file.path.suffix}"
        send_data["caption"] = piece.file.path.name

    message_telegram = client.send_document(**send_data)
    logger.info(f"Pieza subida correctamente: {piece.filename}")

    try:
        message = Message.create(
            message_id=message_telegram.id,
            chat_id=message_telegram.chat.id,
            json_data=json.loads(str(message_telegram)),
            piece=piece,
        )
        logger.debug(
            f"Registro de mensaje creado en base de datos para pieza {piece.filename}"
        )
        return message
    except Exception as e:
        logger.error(f"Error al registrar mensaje de la pieza {piece.filename}: {e}")
        raise


def save_pieces(chunks: List[Path], file: File) -> List[Piece]:
    logger.info(f"Guardando {len(chunks)} piezas en base de datos…")
    with db.atomic():
        pieces = []
        for path in chunks:
            piece = Piece.create(
                path_str=str(path),
                filename=path.name,
                size=path.stat().st_size,
                file=file,
            )
            logger.debug(f"Pieza registrada en base de datos: {path.name}")
            pieces.append(piece)
    logger.info(f"Se guardaron {len(chunks)} piezas correctamente")
    return pieces


def _get_or_chunked_file(file: File, settings: Settings) -> List[Piece]:
    if file.type != FileCategory.CHUNKED:
        raise Exception(
            f"File category '{file.type.value}' no es '{FileCategory.CHUNKED.value}'"
        )

    if file.get_status() == FileStatus.NEW:
        logger.info(f"Dividiendo archivo en piezas: {file.path.name}…")
        chunks = FileChunker.split_file(file, settings)
        logger.info(f"Archivo dividido correctamente en {len(chunks)} piezas")
        pieces = save_pieces(chunks, file)
        file.status = FileStatus.SPLITTED.value
        file.save()
        logger.debug(f"Estado de archivo actualizado a SPLITTED: {file.path.name}")
        return pieces
    elif file.get_status() == FileStatus.SPLITTED:
        logger.debug(f"Obteniendo piezas ya registradas para {file.path.name}")
        return file.pieces
    raise Exception("File status no es 'new' o 'splitted'")


def handle_pieces_file(client, settings: Settings, file: File):
    logger.info(f"Iniciando subida de archivo dividido: {file.path.name}")

    if file.get_status() == FileStatus.UPLOADED:
        logger.info(f"El archivo {file.path.name} ya fue subido a Telegram")
        return

    pieces = _get_or_chunked_file(file, settings)
    for piece in pieces:
        upload_piece_to_telegram(client, settings, piece)

    file.status = FileStatus.UPLOADED.value
    file.save()
    logger.info(f"Archivo completo subido en piezas: {file.path.name}")
    return


def get_or_create_file_records(paths: List[Path], settings: Settings) -> List[File]:
    logger.info(f"Procesando {len(paths)} archivos encontrados")
    file_records = []
    for path in paths:
        if not path.exists():
            logger.debug(f"El path {path} no existe, se omite")
            continue
        elif path.is_dir():
            logger.debug(f"El path {path} es un directorio, se omite")
            continue
        elif settings.is_excluded(path):
            logger.debug(f"El archivo {path.name} está excluido por configuración")
            continue

        file = _get_or_create_file_record(path, settings)
        file_records.append(file)
    logger.info(f"Se registraron {len(file_records)} archivos válidos para subir")
    return file_records


def main():
    setup_logging(f"{__file__}.log", logging.DEBUG)

    logger.info("Iniciando proceso de subida de archivos")
    settings = get_settings("env/test.env")
    target = Path(
        r"D:\\github Leo\\toTelegram\\tests\\Otan Mian Anoixi (Live - Bonus Track)-(240p).mp4"
    )

    init_database(settings)

    paths = list(target.glob("*")) if target.is_dir() else [target]
    file_records = get_or_create_file_records(paths, settings)

    client = None
    for file in file_records:
        if file.get_status() == FileStatus.UPLOADED:
            logger.info(
                f"El archivo {file.path.name} ya estaba marcado como subido, se omite"
            )
            continue

        if client is None:
            client = init_telegram_client(settings)

        if file.type == FileCategory.SINGLE:
            upload_single_file(client, settings, file)
            file.status = FileStatus.UPLOADED.value
            file.save()
            logger.debug(f"Estado de archivo actualizado a UPLOADED: {file.path.name}")
        else:
            handle_pieces_file(client, settings, file)

    logger.info(f"Proceso completado. {len(file_records)} archivos procesados")


if __name__ == "__main__":
    main()
