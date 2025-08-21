# uploader/handlers.py
import json
import logging
from pathlib import Path
from typing import Optional, Tuple, Union
from venv import logger
from totelegram.logging_config import setup_logging
from totelegram.models import File, FileCategory, FileStatus, Message, Piece
from totelegram.setting import Settings, get_settings
from totelegram.uploader.database import get_or_create_file_records, init_database
from totelegram.uploader.telegram import TelegramService, init_telegram_client



def _build_names(path:Path, md5sum:str, settings:Settings)->Tuple[Optional[str], Optional[str]]:        
    if len(path.name) >= settings.max_filename_length:
        logger.debug(f"El nombre de {path.name} excede el límite, se usará md5sum")
        filename = f"{md5sum}.{path.suffix}"
        caption = path.name
        return filename, caption
    return None, None 


def upload_file(record: Union[File, Piece] , service: TelegramService, settings: Settings):
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
    
    tg_message = service.send_file(record.path, filename, caption)
    
    message_data={
        "message_id": tg_message.id,
        "chat_id": tg_message.chat.id,
        "json_data": json.loads(str(tg_message)),
    }
    message_data.update(field)
    return Message.create(**message_data)


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

    telegram = None
    for file in file_records:
        if file.get_status() == FileStatus.UPLOADED:
            logger.info(
                f"El archivo {file.path.name} ya estaba marcado como subido, se omite"
            )
            continue

        if telegram is None:
            client = init_telegram_client(settings)
            telegram= TelegramService(client, settings)

        if file.type == FileCategory.SINGLE:
            upload_file(file, telegram, settings)
            file.status = FileStatus.UPLOADED.value
            file.save()
            logger.debug(f"Estado de archivo actualizado a UPLOADED: {file.path.name}")
    #     else:
    #         handle_pieces_file(client, settings, file)

    # logger.info(f"Proceso completado. {len(file_records)} archivos procesados")

