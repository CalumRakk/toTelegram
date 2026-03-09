from pathlib import Path
from typing import List

from totelegram.identity import Settings
from totelegram.schemas import ScanReport
from totelegram.utils import has_snapshot, is_excluded


class InventoryEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.patterns = settings.all_exclusion_patterns()
        self.max_size = settings.max_filesize_bytes

    def _validate_file(
        self, path: Path, report: ScanReport, check_snapshot: bool
    ) -> bool:
        """
        Lógica para ARCHIVOS.
        Comprueba: Patrones, Tamaño y (opcionalmente) Snapshot.
        """
        if is_excluded(path, self.patterns):
            report.log_skip(path, "exclusion")
            return False

        if check_snapshot and has_snapshot(path):
            report.log_skip(path, "snapshot")
            return False

        if path.stat().st_size > self.max_size:
            report.log_skip(path, "size")
            return False

        return True  # Es un archivo válido

    def _validate_container(self, path: Path, report: ScanReport) -> bool:
        """
        Comprueba Patrones y Snapshot de la carpeta. No comprueba tamaño.
        """
        # Si la carpeta está en la lista de exclusión (ej: node_modules), se salta entera.
        if is_excluded(path, self.patterns):
            report.log_skip(path, "exclusion")
            return False

        # Si la carpeta ya fue archivada como tal.
        if has_snapshot(path):
            report.log_skip(path, "snapshot")
            return False

        return True  # Es una carpeta válida para procesar

    def scan_granular(self, paths: List[Path]) -> ScanReport:
        """MODO EXPLOSIÓN: Filtra archivos, explota carpetas y filtra sus hijos."""
        report = ScanReport(exclusion_patterns=self.patterns)
        for p in paths:
            if p.is_file():
                if self._validate_file(p, report, check_snapshot=True):
                    report.found.append(p)
            elif p.is_dir():
                for sub_p in p.rglob("*"):
                    if sub_p.is_file():
                        if self._validate_file(sub_p, report, check_snapshot=True):
                            report.found.append(sub_p)
        return report

    def scan_backup_inventory(self, paths: List[Path]) -> ScanReport:
        """MODO CONTENEDOR (Fase 1): Filtra solo las carpetas raíz."""
        report = ScanReport()
        for p in paths:
            if p.is_dir():
                if self._validate_container(p, report):
                    report.found.append(p)
        return report

    def scan_backup_internal(self, folder: Path) -> ScanReport:
        """MODO INTERNO (Fase 2): Filtra el contenido de una carpeta para la cinta."""
        report = ScanReport(exclusion_patterns=self.patterns)
        for p in folder.rglob("*"):
            if p.is_file():
                # AQUÍ check_snapshot=False por lo que hablamos de la integridad.
                if self._validate_file(p, report, check_snapshot=False):
                    report.found.append(p)
        return report
