import logging
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, cast

import tartape

from totelegram import __version__
from totelegram.common.enums import SourceType
from totelegram.common.schemas import Inventory
from totelegram.manager.models import SourceFile

logger = logging.getLogger(__name__)


class ArchiveService:
    """
    Responsable de identificar carpetas, gestionar su inventario (T0)
    y validar su integridad para la reanudación.
    """

    system_excludes = [
        "*.json.xz",
        "*.db",
        "*.db-wal",
        "*.db-shm",
        ".DS_Store",
        "Thumbs.db",
    ]

    def get_or_create_session(
        self, folder_path: Path, work_dir: Path, exclusion_patterns: List[str]
    ) -> Tuple[SourceFile, bool, bool]:

        final_excludes = exclusion_patterns + self.system_excludes
        tape = tartape.create(str(folder_path), exclude=final_excludes)

        existing_source = cast(
            SourceFile,
            SourceFile.get_or_none(SourceFile.path_str == str(folder_path)),
        )

        if existing_source:
            # Comparamos contra el fingerprint nativo de TarTape
            if existing_source.md5sum == tape.fingerprint:
                return existing_source, True, False
            else:
                return existing_source, True, True

        # 2. Crear el inventario usando los datos de TarTape
        assert tape._catalog is not None
        current_inventory = Inventory(
            fingerprint=tape.fingerprint,
            total_size=tape.total_size,
            total_files=tape.count_files,
            scan_version=tartape.__version__,
            scan_date=datetime.timestamp(datetime.now()),
            db_path=str(tape.path),
        )

        source = SourceFile.create(
            path_str=str(folder_path),
            md5sum=current_inventory.fingerprint,
            size=current_inventory.total_size,  # Tamaño real del stream TAR
            mtime=current_inventory.scan_date,
            mimetype="application/x-tar",
            inventory=current_inventory,
            type=SourceType.FOLDER,
        )
        return source, False, False
