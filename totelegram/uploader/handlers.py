# uploader/handlers.py
import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple, Union
from venv import logger
from totelegram.filechunker import FileChunker
from totelegram.logging_config import setup_logging
from totelegram.models import File, FileCategory, FileStatus, Message, Piece
from totelegram.setting import Settings, get_settings
from totelegram.uploader.database import get_or_create_file_records, init_database, save_pieces
from totelegram.uploader.telegram import init_telegram_client

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



def _build_names(path:Path, md5sum:str, settings:Settings)->Tuple[Optional[str], Optional[str]]:        
    if len(path.name) >= settings.max_filename_length:
        logger.debug(f"El nombre de {path.name} excede el límite, se usará md5sum")
        filename = f"{md5sum}.{path.suffix}"
        caption = path.name
        return filename, caption
    return None, None 


def upload_file(client, record: Union[File, Piece], settings: Settings):
    logger.info(f"Subiendo archivo único: {record.path}…")

    if isinstance(record, File):
        md5sum= record.md5sum
        model_field= "file"
    elif isinstance(record, Piece):
        md5sum= record.file.md5sum
        model_field= "piece"
    else:
        raise ValueError("record debe ser de tipo File o Piece")
    
    field= {model_field: record}

    filename, caption = _build_names(record.path, md5sum, settings)
    send_data = {
        "chat_id": settings.chat_id, 
        "document": str(record.path),
        "file_name": filename,
        "caption": caption
    }
    tg_message = client.send_document(**send_data)
    
    message_data={
        "message_id": tg_message.id,
        "chat_id": tg_message.chat.id,
        "json_data": json.loads(str(tg_message)),
    }
    message_data.update(field)
    return Message.create(**message_data)

def handle_pieces_file(client, settings: Settings, file: File):
    logger.info(f"Procesando el archivo: {file.path.name}")

    if file.get_status() == FileStatus.UPLOADED:
        logger.info(f"El archivo {file.path.name} ya fue subido a Telegram")
        return

    pieces = _get_or_chunked_file(file, settings)
    for piece in pieces:
        upload_file(client, piece, settings)

    file.status = FileStatus.UPLOADED.value
    file.save()
    logger.info(f"Archivo completo subido en piezas: {file.path.name}")
    return


def main(target:Path, settings:Settings):   
    logger.info("Iniciando proceso de subida de archivos")
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
            upload_file(client, file, settings)
            file.status = FileStatus.UPLOADED.value
            file.save()
            logger.debug(f"Estado de archivo actualizado a UPLOADED: {file.path.name}")
        else:
            handle_pieces_file(client, settings, file)

    logger.info(f"Proceso completado. {len(file_records)} archivos procesados")

