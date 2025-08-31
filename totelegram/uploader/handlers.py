# uploader/handlers.py
import json
import lzma
from pathlib import Path
from totelegram.models import db_proxy
from typing import List, Optional, Tuple, Union
from venv import logger
from datetime import datetime
from pyrogram.types.messages_and_media.message import Message as MessageTg
from totelegram.filechunker import FileChunker
from totelegram.models import File, FileCategory, FileStatus, Message, Piece
from totelegram.schemas import ManagerPieces, ManagerSingleFile, Snapshot
from totelegram.setting import Settings
from totelegram.uploader.database import (
    get_or_create_file_records,
    init_database,
    save_pieces,
)
from totelegram.uploader.telegram import init_telegram_client, is_empty_message
from typing import Optional

_last_percentage = {}

def progress_bar(current: int, total: int, filename: Optional[str] = None):
    porcentage = int(current * 100 / total)
    
    # clave única por archivo
    key = filename or "_default"

    # solo loguea si es múltiplo de 5 y no se repite
    if porcentage % 2 == 0 and _last_percentage.get(key) != porcentage:
        _last_percentage[key] = porcentage
        logger.info(
            f"Subiendo el archivo {filename} "
            f"({current} de {total}) {porcentage}%"
        )

def get_or_chunked_file(file: File, settings: Settings) -> List[Piece]:
    if file.type != FileCategory.CHUNKED:
        raise Exception(
            f"File category '{file.type.value}' no es '{FileCategory.CHUNKED.value}'"
        )

    if file.get_status() == FileStatus.PENDING:
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


def _build_names(
    path: Path, md5sum: str, settings: Settings
) -> Tuple[Optional[str], Optional[str]]:
    if len(path.name) >= settings.max_filename_length:
        logger.debug(f"El nombre de {path.name} excede el límite, se usará md5sum")
        filename = f"{md5sum}.{path.suffix}"
        caption = path.name
        return filename, caption
    return None, None

def _get_model_field_and_md5sum(record: Union[File, Piece]):
    if isinstance(record, File):
        model_field = "file"
        md5sum = record.md5sum
    elif isinstance(record, Piece):
        model_field = "piece"
        md5sum = record.file.md5sum
    else:
        raise ValueError("record debe ser de tipo File o Piece")
    return model_field, md5sum

def upload_file(client, record: Union[File, Piece], settings: Settings):
    logger.info("="*50)

    model_field, md5sum = _get_model_field_and_md5sum(record)
    filename, caption = _build_names(record.path, md5sum, settings)

    # Información del chat
    chat_info= client.get_chat(settings.chat_id)    
    logger.info(f"Chat: {chat_info.title}")
    
    # Subir el archivo
    tg_message = client.send_document(
        chat_id=settings.chat_id,
        document=str(record.path),
        file_name=filename, 
        caption=caption, 
        progress=progress_bar, 
        progress_args=(record.path.name,)
    )
    
    # Vincular SingleFile o Piece con el mensaje enviado a Telegram
    message_data = {
        "message_id": tg_message.id,
        "chat_id": tg_message.chat.id,
        "json_data": json.loads(str(tg_message)),
        model_field: record
    }
    Message.create(**message_data)        

    # Actualizar el estado
    if isinstance(record, Piece):
        logger.info(f"Pieza subida correctamente: {record.path.name}")
        record.is_uploaded = True
        record.save()
        record.path.unlink(missing_ok=True)
        logger.debug(f"Se borra el archivo temporal: {record.path}")
    else:
        logger.info(f"Archivo único subido correctamente: {record.path.name}")           
        record.status = FileStatus.UPLOADED.value
        record.save()


def handle_pieces_file(client, settings: Settings, file: File):
    logger.info(f"Procesando el archivo: {file.path.name}")

    if file.get_status() == FileStatus.UPLOADED:
        logger.info(f"El archivo {file.path.name} ya fue subido a Telegram")
        return

    pieces = get_or_chunked_file(file, settings)
    for piece in pieces:
        if not piece.is_uploaded:
            upload_file(client, piece, settings)

    file.status = FileStatus.UPLOADED.value
    file.save()
    logger.info(f"Archivo completo subido en piezas: {file.path.name}")
    return


def generate_snapshot(file: File):
    output = file.path.with_suffix(".json.xz")
    logger.info(f"Generando snapshot de {file.path.name}…")

    from totelegram.schemas import File as FileSchema
    from totelegram.schemas import Message as MessageSchema
    from totelegram.schemas import Piece as PieceSchema
    ifile = FileSchema(
            kind="file",
            filename=file.filename,
            fileExtension=file.path.suffix,
            mimeType=file.mimetype,
            md5sum=file.md5sum,
            size=file.size,
            medatada={},
        )
    if file.type == FileCategory.SINGLE:
        manager_kind = FileCategory.SINGLE.value

        tg_message= file.message.get_message()
        media:dict= getattr(tg_message, tg_message.media.value) # type: ignore
        message = MessageSchema(
            message_id=tg_message.id,
            chat_id=tg_message.chat.id,
            link=tg_message.link,
            file_name=media["file_name"],
            size= media["file_size"],
        )

        manager = ManagerSingleFile(kind=manager_kind, file=ifile, message=message)
        
    elif file.type == FileCategory.CHUNKED:
        manager_kind = FileCategory.CHUNKED.value
        pieces=[]
        for piece in file.pieces:
            tg_message= piece.message.get_message()
            media:dict= getattr(tg_message, tg_message.media.value) # type: ignore
            message = MessageSchema(
                message_id=tg_message.id,
                chat_id=tg_message.chat.id,
                link=tg_message.link,
                file_name=media["file_name"],
                size= media["file_size"],
            )
            ipiece= PieceSchema(kind="#piece", filename=piece.filename, size=piece.size, message=message)
            pieces.append(ipiece)
        manager = ManagerPieces(kind=manager_kind, file=ifile, pieces=pieces)    
    else:
        raise Exception(f"Tipo de archivo desconocido: {file.type}")


    snapshot= Snapshot(
        kind=manager_kind,
        manager=manager,
        createdTime=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )        
    
    with lzma.open(output, "wt") as f:
        json.dump(snapshot.model_dump(), f)
    logger.info(f"Snapshot de {file.path.name} generado correctamente")
def mark_file_as_orphan(client, file:File):
    """Marca como orphan (huerfano) un archivo que no se pudo subir a Telegram
    
    Nota: Si el archivo es CHUNKED, marca el archivo como orphan y actualiza el estado de las pieces en `is_uploaded=False` solo las que no se encuentren en Telegram."""
    if file.get_category() == FileCategory.SINGLE:
        file.status = FileStatus.ORPHANED.value
        file.save()
    elif file.get_category() == FileCategory.CHUNKED:
        to_update=[]
        for piece in file.pieces:
            message_id= piece.message.message_id
            chat_id= piece.message.chat_id
            message: MessageTg= client.get_messages(chat_id, message_id) # type: ignore
            if message.empty:
                piece.is_uploaded=False
                to_update.append(piece)        
        with db_proxy.atomic():     
            for piece in to_update:             
                piece.is_uploaded=False
                piece.save()
                piece.message.delete_instance()
            file.status = FileStatus.ORPHANED.value
            file.save()       
    else:
        raise Exception(f"Tipo de archivo desconocido: {file.type}")

def upload(target: Path, settings: Settings)-> List[File]:
    target= Path(target) if isinstance(target, str) else target
    logger.info("Iniciando proceso de subida de archivos")
    init_database(settings)

    paths = list(target.glob("*")) if target.is_dir() else [target]
    file_records = get_or_create_file_records(paths, settings)

    client = init_telegram_client(settings)
    results=[]
    for file in file_records:
        if file.get_status() == FileStatus.UPLOADED:
            if is_empty_message(client, file):
                logger.info(f"El archivo está marcado como subido, pero no se encontró en Telegram. Se volverá a subir")
                mark_file_as_orphan(client, file)
            else:
                logger.info(
                    f"El archivo {file.path.name} ya estaba marcado como subido, se omite"
                )
                generate_snapshot(file)
                continue

        if file.type == FileCategory.SINGLE:
            upload_file(client, file, settings)
        else:
            handle_pieces_file(client, settings, file)

        generate_snapshot(file)
        results.append(file)

    logger.info(f"Proceso completado. {len(file_records)} archivos procesados")
    return results

