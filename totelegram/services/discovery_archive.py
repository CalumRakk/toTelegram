import hashlib
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Optional, Tuple

from tartape import TarTape
from totelegram import __version__
from totelegram.core.schemas import Inventory
from totelegram.store.models import SourceFile

logger = logging.getLogger(__name__)


class ArchiveDiscoveryService:
    """
    Responsable de identificar carpetas, gestionar su inventario (T0)
    y validar su integridad para la reanudación.
    """

    def __init__(
        self,
        root_path: Path,
        work_dir: Path,
        exclusion_patterns: Optional[List[str]] = None,
    ):
        self.root_path = root_path.absolute()
        self.work_dir = work_dir / "inventories"
        self.exclusion_patterns = exclusion_patterns or []

        # TODO : Esto deberia sacarse de la config por defecto.
        self.system_excludes = [
            "*.json.xz",
            "*.db",
            "*.db-wal",
            "*.db-shm",
            ".DS_Store",
            "Thumbs.db",
        ]

    def discover_or_create_session(self) -> Tuple[SourceFile, bool]:
        """
        Punto de entrada principal.
        - Escanea la carpeta para obtener la firma actual.
        - Busca si ya existe una sesión con esa firma en la DB.
        - Si existe, la devuelve (Resume). Si no, crea una nueva.

        Returns: (Sesion, es_reanudacion)
        """

        with TemporaryDirectory() as tmpdirname:
            temp_db_path = Path(tmpdirname) / "inventory.db"

            fingerprint, count, total_size = self._create_inventory(temp_db_path)

            existing_source: Optional[SourceFile] = SourceFile.get_or_none(
                SourceFile.md5sum == fingerprint
            )

            if existing_source:
                logger.info(
                    f"Carpeta identificada por firma: {fingerprint[:10]}... (Reanudando)"
                )

                # TODO:Aqui deberia haber una comprobacion si existe la db en la ruta determinista
                return existing_source, True

            session_id = uuid.uuid4()
            final_db_path = self.work_dir / f"{fingerprint}.db"
            final_db_path.parent.mkdir(parents=True, exist_ok=True)
            temp_db_path.rename(final_db_path)

            new_session = SourceFile.create(
                id=session_id,
                path_str=str(self.root_path),
                fingerprint=fingerprint,
                tartape_db_path=str(final_db_path),
                total_files=count,
                total_size=total_size,
                app_version=__version__,
            )

            return new_session, False

    def _create_inventory(self, db_path: Path) -> Inventory:
        """
        Ejecuta tartape para indexar la carpeta y genera el fingerprint.

        Returns: (fingerprint, count, total_size)
        """
        tape = TarTape(index_path=str(db_path), anonymize=True)

        final_excludes = self.exclusion_patterns + self.system_excludes

        tape.add_folder(self.root_path, recursive=True, exclude=final_excludes)

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
        )

    def verify_integrity(self, session: SourceFile) -> bool:
        """
        Verificación de seguridad antes de cada subida.
        Compara la firma actual del disco con la de la sesión.
        """
        with TemporaryDirectory() as tmpdirname:
            temp_db = Path(tmpdirname) / "integrity_check.db"

            current_fp, _, _ = self._create_inventory(temp_db)
            is_ok = current_fp == session.md5sum
            if not is_ok:
                logger.error(
                    "La estructura de la carpeta ha cambiado. La cinta de datos es invalida."
                )
            return is_ok
