import hashlib
import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from pydantic import BaseModel

from totelegram import __version__
from totelegram.core.enums import ArchiveStatus
from totelegram.store.models import ArchiveSession
from totelegram.utils import is_excluded

logger = logging.getLogger(__name__)


class ArchiveInventoryItem(BaseModel):
    """Representación en memoria de un archivo durante el escaneo T0."""

    relative_path: str
    size: int
    mtime: float


class ArchiveDiscoveryService:
    def __init__(self, root_path: Path, exclusion_patterns: Optional[List[str]] = []):
        self.root_path = root_path
        self.exclusion_patterns = exclusion_patterns or []

    def _is_excluded(self, p: Path) -> bool:
        if not p.is_file():
            return True

        if is_excluded(p, self.exclusion_patterns):
            return True

        if p.name.endswith(".json.xz"):
            return True

        return False

    def run_initial_scan(self) -> Tuple[ArchiveSession, List[ArchiveInventoryItem]]:
        """
        Realiza el inventario completo (T0) y genera el Fingerprint.
        """
        logger.info(f"Iniciando inventario T0 en: {self.root_path}")

        inventory: List[ArchiveInventoryItem] = []
        total_size = 0

        for p in self.root_path.rglob("*"):
            if self._is_excluded(p):
                continue

            rel_p = p.relative_to(self.root_path).as_posix()
            stat = p.stat()

            item = ArchiveInventoryItem(
                relative_path=rel_p, size=stat.st_size, mtime=stat.st_mtime
            )
            inventory.append(item)
            total_size += stat.st_size

        # Ordenamiento determinista (Vital para el Fingerprint)
        inventory.sort(key=lambda x: x.relative_path)
        fingerprint = self._compute_structural_fingerprint(inventory)

        logger.info(
            f"Escaneo T0 completado. Archivos: {len(inventory)}, Fingerprint: {fingerprint[:10]}..."
        )

        session = ArchiveSession.create(
            root_path=str(self.root_path),
            fingerprint=fingerprint,
            total_files=len(inventory),
            total_size=total_size,
            status=ArchiveStatus.PENDING,
            app_version=__version__,
        )

        return session, inventory

    def verify_fingerprint(self, session: ArchiveSession) -> bool:
        """
        Verifica si la carpeta actual coincide con el Fingerprint guardado.
        Se usa antes de empezar la subida real (Pre-flight check).
        """
        _, current_inventory = self.run_initial_scan_memory_only()
        current_fingerprint = self._compute_structural_fingerprint(current_inventory)

        return current_fingerprint == session.fingerprint

    def run_initial_scan_memory_only(self) -> Tuple[str, List[ArchiveInventoryItem]]:
        """Versión ligera de escaneo que no toca la base de datos."""
        inventory: List[ArchiveInventoryItem] = []
        for p in self.root_path.rglob("*"):
            if not self._is_excluded(p):
                rel_p = p.relative_to(self.root_path).as_posix()
                stat = p.stat()
                inventory.append(
                    ArchiveInventoryItem(
                        relative_path=rel_p, size=stat.st_size, mtime=stat.st_mtime
                    )
                )
        inventory.sort(key=lambda x: x.relative_path)
        return self._compute_structural_fingerprint(inventory), inventory

    def _compute_structural_fingerprint(
        self, inventory: List[ArchiveInventoryItem]
    ) -> str:
        """
        Calcula un SHA256 basado exclusivamente en la estructura y metadatos.
        Si un solo mtime cambia o un archivo se mueve, el hash será distinto.
        """

        struct_data = [item.model_dump() for item in inventory]
        struct_json = json.dumps(struct_data, sort_keys=True)

        return hashlib.sha256(struct_json.encode()).hexdigest()
