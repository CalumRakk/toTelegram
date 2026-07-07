import keyword
import logging
import uuid
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def get_node_id(worktable: Path) -> str:
    """Genera o recupera un identificador único para esta máquina."""
    node_id_file = worktable / "node_id"
    if not node_id_file.exists():
        node_id = str(uuid.uuid4())
        node_id_file.write_text(node_id)
        return node_id
    return node_id_file.read_text().strip()


def is_excluded(path: Path, patterns: List[str]) -> bool:
    """Devuelve True si el path debe ser excluido según las reglas."""
    if not path.exists():
        logger.info(f"No existe: {path}, se omite")
        return True

    for pattern in patterns:
        if path.match(pattern):
            logger.info(f"Está excluido por configuración: {path}, se omite")
            return True

        for parent in path.parents:
            if str(parent) == ".":
                break
            if parent.match(pattern):
                logger.info(f"Está excluido por configuración: {path}, se omite")
                return True

    logger.info(f"No está excluido por configuración: {path}")
    return False


def has_snapshot(file_path: Path) -> bool:
    filename_plus_ext = file_path.with_name(f"{file_path.name}.json.xz")
    stem_plus_ext = file_path.with_name(f"{file_path.stem}.json.xz")
    return filename_plus_ext.exists() or stem_plus_ext.exists()


def delete_snapshot(file_path: Path):
    """Elimina los posibles archivos de snapshot asociados a una ruta."""
    targets = [
        file_path.with_name(f"{file_path.name}.json.xz"),
        file_path.with_name(f"{file_path.stem}.json.xz"),
    ]
    for target in targets:
        if target.exists():
            target.unlink()
            logger.debug(f"Snapshot eliminado físicamente: {target.name}")


def is_valid_profile_name(profile_name: str) -> bool:
    return profile_name.isidentifier() and not keyword.iskeyword(profile_name)


def is_suspected_glob_expansion(values: List[str]) -> bool:
    """Devuelve True si la terminal expandió un comodín localmente."""
    if not values:
        return False

    has_wildcard = any("*" in v or "?" in v for v in values)
    if has_wildcard:
        return False

    existing_files = sum(1 for v in values if Path(v).exists())

    if len(values) > 1 and existing_files == len(values):
        return True

    if len(values) == 1 and existing_files == 1:
        return True

    return False


def validate_item(value: str) -> str:
    if "," in value or value.strip().startswith("["):
        raise ValueError("Formato no soportado.")
    return value.strip("'").strip('"')
