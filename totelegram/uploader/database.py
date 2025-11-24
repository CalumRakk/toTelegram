import json
import logging
from pathlib import Path
from typing import List, Union

import peewee

from totelegram.models import File, FileCategory, MessageDB, Piece, db_proxy
from totelegram.setting import Settings
from totelegram.utils import create_md5sum_by_hashlib, get_mimetype, is_excluded

logger = logging.getLogger(__name__)


def register_upload_success(record: Union[File, Piece], tg_message) -> MessageDB:
    """
    Registra en la BD que un archivo o pieza se subió exitosamente y guarda la ref del mensaje.
    Recibe el objeto tg_message de Pyrogram (o cualquier objeto con .id, .chat.id y __str__).
    """
    logger.debug(f"Registrando mensaje exitoso para: {record.filename}")

    data = {
        "message_id": tg_message.id,
        "chat_id": tg_message.chat.id,
        "json_data": json.loads(str(tg_message)),
    }

    if isinstance(record, File):
        data["file"] = record
    elif isinstance(record, Piece):
        data["piece"] = record
    else:
        raise ValueError(f"Tipo de registro no soportado: {type(record)}")

    try:
        message_db = MessageDB.create(**data)
        return message_db
    except Exception as e:
        logger.error(f"Error al guardar mensaje en BD para {record.filename}: {e}")
        raise


def init_database(settings: Settings):
    logger.info(f"Iniciando base de datos en {settings.database_path}")
    database = peewee.SqliteDatabase(str(settings.database_path))

    db_proxy.initialize(database)

    db_proxy.create_tables([Piece, MessageDB, File], safe=True)
    logger.info("Base de datos inicializada correctamente")
    db_proxy.close()


def _get_or_create_file_record(path: Path, settings: Settings) -> File:
    # 1. Obtener metadatos actuales del sistema de archivos
    stat = path.stat()
    current_size = stat.st_size
    current_mtime = stat.st_mtime
    path_str = str(path)

    # Buscamos si existe un registro que coincida en ruta, tamaño y fecha.
    cached_file = File.get_or_none(
        (File.path_str == path_str)
        & (File.size == current_size)
        & (File.mtime == current_mtime)
    )
    if cached_file:
        logger.debug(
            f"CACHE HIT: {path.name} detectado por metadatos (mtime). Saltando cálculo MD5."
        )
        return cached_file

    md5sum = create_md5sum_by_hashlib(path)
    file = File.get_or_none(md5sum=md5sum)

    if file is not None:
        logger.debug(f"El archivo ya existía por MD5 (posible cambio de ruta/fecha)")
        changed = False
        if file.path_str != path_str:
            file.path_str = path_str
            changed = True

        if file.mtime != current_mtime:
            file.mtime = current_mtime
            changed = True

        if file.size != current_size:
            file.size = current_size
            changed = True

        if changed:
            file.save()
            logger.debug("Metadatos actualizados en BD.")
        return file

    logger.debug(f"Creando nuevo registro en BD para {path.name}")
    mimetype = get_mimetype(path)

    file_data = {
        "path_str": path_str,
        "filename": path.name,
        "size": current_size,
        "mimetype": mimetype,
        "md5sum": md5sum,
        "mtime": current_mtime,
    }

    if current_size <= settings.max_filesize_bytes:
        file_data["category"] = FileCategory.SINGLE.value
        logger.debug(f"El path {path.name} es de tipo SINGLE")
    else:
        file_data["category"] = FileCategory.CHUNKED.value
        logger.debug(f"El path {path.name} es de tipo CHUNKED")

    file = File.create(**file_data)
    return file


def save_pieces(chunks: List[Path], file: File) -> List[Piece]:
    logger.info(f"Guardando {len(chunks)} piezas en base de datos…")
    with db_proxy.atomic():
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
    if len(paths) == 0:
        logger.info("No se especificaron paths, se omite")
        return []
    elif len(paths) == 1:
        logger.info(f"Obteniendo file_record del path especificado...")
    else:
        logger.info(
            f"Obteniendo file_records de los {len(paths)} paths especificados..."
        )

    file_records = []
    for path in paths:
        logger.info(f"Procesando path: {path}")
        if is_excluded(path, settings):
            logger.info(f"Path excluido, se omite: {path}")
            continue

        file = _get_or_create_file_record(path, settings)
        file_records.append(file)

    if len(file_records) == 0:
        logger.info("No se obtuvinieron file_records")
    elif len(file_records) == 1:
        logger.info(f"Se obtuvo {len(file_records)} file_record")
    else:
        logger.info(f"Se obtuvieron {len(file_records)} file_records")
    return file_records
