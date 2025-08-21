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
