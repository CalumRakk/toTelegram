import logging
from pathlib import PurePath

from tartape.units import format_size

from totelegram.schemas import SourceType

logger = logging.getLogger(__name__)

# Límites de la plataforma Telegram
MAX_TELEGRAM_CAPTION_LENGTH = 1024
SAFE_FILENAME_MAX_LENGTH = 80

# Etiquetas canónicas del protocolo
TAG_ROOT_CHUNK = "#totelegram_chunk"
TAG_ROOT_MANIFEST = "#totelegram_manifest"
TAG_TYPE_FILE = "#type_file"
TAG_TYPE_TAPE = "#type_tape"


def truncate_middle(text: str, max_chars: int = SAFE_FILENAME_MAX_LENGTH) -> str:
    """
    Trunca una cadena larga preservando el inicio y la extensión final si existe.
    Ejemplo: 'archivo_gigante_con_nombre_larguisimo.tar.01' -> 'archivo_gigante...tar.01'
    """
    if len(text) <= max_chars:
        return text

    path = PurePath(text)
    ext = "".join(path.suffixes)
    stem = text[: -len(ext)] if ext else text

    available_stem = max(10, max_chars - len(ext) - 3)
    half = available_stem // 2

    truncated = f"{stem[:half]}...{stem[-half:]}{ext}"
    return truncated


def build_chunk_caption(
    filename: str,
    sequence_index: int,
    total_parts: int,
    part_size: int,
    master_md5: str,
    part_md5: str | None = None,
) -> str:
    """
    Genera el caption estructurado para una pieza física (Payload).
    Garantiza una longitud menor a 1024 caracteres y la vinculación por #src_<md5>.
    """
    safe_name = truncate_middle(filename, SAFE_FILENAME_MAX_LENGTH)
    current_part = sequence_index + 1
    padding = max(2, len(str(total_parts)))
    part_tag_suffix = (
        f"{str(current_part).zfill(padding)}_{str(total_parts).zfill(padding)}"
    )

    lines = [
        f"📦 {safe_name}",
        f"🧩 Parte [{current_part}/{total_parts}] • {format_size(part_size)}",
    ]

    if part_md5:
        lines.append(f"🔑 MD5 Parte: {part_md5}")

    lines.append(f"🏷 {TAG_ROOT_CHUNK} #part_{part_tag_suffix} #src_{master_md5}")

    caption = "\n".join(lines)

    # Salvaguarda final estricta de longitud
    if len(caption) > MAX_TELEGRAM_CAPTION_LENGTH:
        logger.warning(
            "El caption del chunk excedió 1024 caracteres. Recortando de emergencia."
        )
        caption = caption[:MAX_TELEGRAM_CAPTION_LENGTH]

    return caption


def build_manifest_caption(
    filename: str,
    source_type: SourceType,
    total_size: int,
    total_parts: int,
    master_md5: str,
) -> str:
    """
    Genera el caption de cierre para el Snapshot (.json.xz o .ttape).
    Distingue el tipo de recurso con #type_file o #type_tape.
    """
    safe_name = truncate_middle(filename, SAFE_FILENAME_MAX_LENGTH)
    type_tag = TAG_TYPE_TAPE if source_type == SourceType.FOLDER else TAG_TYPE_FILE
    icon = "📁" if source_type == SourceType.FOLDER else "📄"
    parts_word = "parte" if total_parts == 1 else "partes"

    lines = [
        "✅ BACKUP COMPLETADO",
        f"{icon} Recurso: {safe_name}",
        f"📊 {format_size(total_size)} ({total_parts} {parts_word})",
        f"🔑 MD5 Maestro: {master_md5}",
        f"🏷 {TAG_ROOT_MANIFEST} {type_tag} #src_{master_md5}",
    ]

    caption = "\n".join(lines)

    if len(caption) > MAX_TELEGRAM_CAPTION_LENGTH:
        logger.warning(
            "El caption del snapshot excedió 1024 caracteres. Recortando de emergencia."
        )
        caption = caption[:MAX_TELEGRAM_CAPTION_LENGTH]

    return caption
