import keyword
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


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
    targets = [
        file_path.with_name(f"{file_path.name}.json.xz"),
        file_path.with_name(f"{file_path.stem}.json.xz"),
        file_path.with_name(f"{file_path.name}.ttape"),
        file_path.with_name(f"{file_path.stem}.ttape"),
    ]
    return any(t.exists() for t in targets)


def delete_snapshot(file_path: Path):
    targets = [
        file_path.with_name(f"{file_path.name}.json.xz"),
        file_path.with_name(f"{file_path.stem}.json.xz"),
        file_path.with_name(f"{file_path.name}.ttape"),
        file_path.with_name(f"{file_path.stem}.ttape"),
        file_path.with_name(f"{file_path.name}.tartape"),
        file_path.with_name(f"{file_path.stem}.tartape"),
    ]
    for target in targets:
        if target.exists() and target.is_file():
            target.unlink()


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

    return bool(len(values) == 1 and existing_files == 1)


def validate_item(value: str) -> str:
    if "," in value or value.strip().startswith("["):
        raise ValueError("Formato no soportado.")
    return value.strip("'").strip('"')
