import hashlib
import json
import logging
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Optional, Tuple

from tartape import TarTape
from totelegram import __version__
from totelegram.core.enums import ArchiveStatus
from totelegram.store.models import ArchiveSession

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
        self.work_dir = work_dir / "discovery"
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

    def discover_or_create_session(self) -> Tuple[ArchiveSession, bool]:
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

            existing_session: Optional[ArchiveSession] = ArchiveSession.get_or_none(
                ArchiveSession.fingerprint == fingerprint,
                ArchiveSession.status != ArchiveStatus.COMPLETED,
            )

            if existing_session:
                logger.info(
                    f"Carpeta identificada por firma: {fingerprint[:10]}... (Reanudando)"
                )

                if existing_session.root_path != str(self.root_path):
                    logger.info(
                        f"Detectado cambio de ruta: {existing_session.root_path} -> {self.root_path}"
                    )
                    existing_session.set_root_path(self.root_path)
                return existing_session, True

            session_id = uuid.uuid4()
            final_db_path = self.work_dir / f"inventory_{session_id.hex}.db"
            final_db_path.parent.mkdir(parents=True, exist_ok=True)
            temp_db_path.rename(final_db_path)

            new_session = ArchiveSession.create(
                id=session_id,
                root_path=str(self.root_path),
                fingerprint=fingerprint,
                tartape_db_path=str(final_db_path),
                total_files=count,
                total_size=total_size,
                status=ArchiveStatus.PENDING,
                app_version=__version__,
            )

            return new_session, False

    def _create_inventory(self, db_path: Path) -> Tuple[str, int, int]:
        """
        Ejecuta tartape para indexar la carpeta y genera el fingerprint.
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

        return fingerprint, count, total_size

    def verify_integrity(self, session: ArchiveSession) -> bool:
        """
        Verificación de seguridad antes de cada subida.
        Compara la firma actual del disco con la de la sesión.
        """
        with TemporaryDirectory() as tmpdirname:
            temp_db = Path(tmpdirname) / "integrity_check.db"

            current_fp, _, _ = self._create_inventory(temp_db)
            is_ok = current_fp == session.fingerprint
            if not is_ok:
                logger.error(
                    "La estructura de la carpeta ha cambiado. La cinta de datos es invalida."
                )
            return is_ok
