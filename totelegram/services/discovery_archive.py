import hashlib
import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from tartape import TarTape
from totelegram import __version__
from totelegram.core.enums import ArchiveStatus
from totelegram.store.models import ArchiveSession

logger = logging.getLogger(__name__)


class ArchiveDiscoveryService:
    def __init__(self, root_path: Path, exclusion_patterns: Optional[List[str]] = None):
        self.root_path = root_path
        self.exclusion_patterns = exclusion_patterns or []

    def run_initial_scan(self) -> Tuple[ArchiveSession, str]:
        """
        Realiza el inventario completo (T0) delegando en TarTape y genera el Fingerprint.
        """
        logger.info(f"Iniciando inventario T0 en: {self.root_path} usando TarTape")

        # Usamos TarTape en memoria para obtener el fingerprint.
        # No persistimos el DB: solo necesitamos una “foto”.
        # Así garantizamos las mismas reglas que en la transmisión.
        tape = TarTape(index_path=":memory:", anonymize=True)

        # TODO : Esto deberia sacarse de la config por defecto.
        system_excludes = ["*.json.xz", "*.db", "*.db-wal", "*.db-shm"]
        final_excludes = self.exclusion_patterns + system_excludes

        tape.add_folder(self.root_path, recursive=True, exclude=final_excludes)

        fingerprint, total_files, total_size = self._compute_fingerprint_from_tape(tape)

        logger.info(
            f"Escaneo T0 completado. Archivos: {total_files}, Size: {total_size}, FP: {fingerprint[:10]}..."
        )

        session = ArchiveSession.create(
            root_path=str(self.root_path),
            fingerprint=fingerprint,
            total_files=total_files,
            total_size=total_size,
            status=ArchiveStatus.PENDING,
            app_version=__version__,
        )

        return session, fingerprint

    def verify_fingerprint(self, session: ArchiveSession) -> bool:
        """
        Verifica si la carpeta actual coincide con el Fingerprint guardado.
        Re-escanea usando TarTape y compara hashes.
        """
        logger.info("Verificando integridad estructural...")

        tape = TarTape(index_path=":memory:", anonymize=True)
        system_excludes = ["*.json.xz", "*.db", "*.db-wal", "*.db-shm"]
        # TODO: Asegurar que exclusion_patterns venga de la configuración guardada si es necesario
        final_excludes = self.exclusion_patterns + system_excludes

        tape.add_folder(self.root_path, recursive=True, exclude=final_excludes)

        current_fingerprint, _, _ = self._compute_fingerprint_from_tape(tape)

        is_valid = current_fingerprint == session.fingerprint
        if not is_valid:
            logger.warning(
                f"Fingerprint Mismatch! Guardado: {session.fingerprint[:8]} vs Actual: {current_fingerprint[:8]}"
            )

        return is_valid

    def _compute_fingerprint_from_tape(self, tape: TarTape) -> Tuple[str, int, int]:
        """
        Extrae la esencia estructural del inventario de TarTape.
        Retorna (SHA256, count, total_size)
        """
        struct_data = []
        total_size = 0
        count = 0

        # get_entries() ya retorna ordenado (Determinismo estructural garantizado por tartape)
        for entry in tape._inventory.get_entries():
            item_summary = {
                "p": entry.arc_path,  # Path relativo dentro del TAR
                "s": entry.size,
                "m": entry.mtime,
            }
            struct_data.append(item_summary)
            total_size += entry.size
            count += 1

        struct_json = json.dumps(struct_data, sort_keys=True)
        fingerprint = hashlib.sha256(struct_json.encode()).hexdigest()

        return fingerprint, count, total_size
