import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Tuple, cast

from tartape import TarTape
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

    def __persist_inventory(self, inventory: Inventory) -> None:
        pass

    def get_or_create_session(
        self, folder_path: Path, work_dir: Path, exclusion_patterns: List[str]
    ) -> Tuple[SourceFile, bool, bool]:
        """
        Analiza la carpeta y la compara con la base de datos.

        Returns:
            (SourceFile, is_resume, is_modified)
            - is_resume: True si la ruta ya existía en la DB.
            - is_modified: True si la ruta existía pero el contenido actual es distinto.
        """
        with TemporaryDirectory() as tmpdirname:
            temp_db_path = Path(tmpdirname) / "inventory_temp.db"
            current_inventory = self._create_inventory(
                folder_path, temp_db_path, exclusion_patterns
            )

            existing_source = cast(
                SourceFile,
                SourceFile.get_or_none(SourceFile.path_str == str(folder_path)),
            )
            if existing_source:
                if existing_source.md5sum == current_inventory.fingerprint:
                    return existing_source, True, False
                else:
                    # La ruta existe pero el contenido cambió
                    return existing_source, True, True

            # Si no existe, crear el registro oficial
            # Luego, movemos la DB temporal a su lugar definitivo
            final_db_path = work_dir / f"{current_inventory.fingerprint}.db"
            final_db_path.parent.mkdir(parents=True, exist_ok=True)

            if final_db_path.exists():  # Caso casi imposible, pero aseguramos.
                final_db_path.unlink()

            temp_db_path.rename(final_db_path)
            current_inventory.db_path = str(final_db_path)

            source = SourceFile.create(
                path_str=str(folder_path),
                md5sum=current_inventory.fingerprint,
                size=current_inventory.total_files,
                mtime=current_inventory.scan_date,
                mimetype="application/x-tar",
                inventory=current_inventory,
                type=SourceType.FOLDER,
            )
            return source, False, False

    def _create_inventory(
        self, folder_path: Path, db_path: Path, exclusion_patterns: List[str] = list()
    ) -> Inventory:
        """
        Ejecuta tartape para indexar la carpeta y genera el fingerprint.
        """
        tape = TarTape(index_path=str(db_path), anonymize=True)

        final_excludes = exclusion_patterns + self.system_excludes

        tape.add_folder(folder_path, recursive=True, exclude=final_excludes)

        struct_data = []
        total_size = 0
        count = 0

        for entry in tape._inventory.get_entries():
            item_summary = {
                "p": entry.arc_path,
                "s": entry.size,
                "m": entry.mtime,
            }
            struct_data.append(item_summary)
            total_size += entry.size
            count += 1

        struct_json = json.dumps(struct_data, sort_keys=True)
        fingerprint = hashlib.sha256(struct_json.encode()).hexdigest()
        scan_date = datetime.now().timestamp()
        return Inventory(
            fingerprint=fingerprint,
            total_size=total_size,
            total_files=count,
            scan_version=__version__,
            scan_date=scan_date,
            db_path=str(db_path),
        )
