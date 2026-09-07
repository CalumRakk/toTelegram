import hashlib
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

from totelegram.packaging.schemas import RemotePart, UploadManifest
from totelegram.schemas import SourceType

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Message

logger = logging.getLogger(__name__)


class DownloadIntegrityError(Exception):
    """Lanzada cuando un archivo o fragmento no coincide con el checksum esperado."""

    pass


@dataclass
class DownloadReport:
    """Resultado consolidado de una operación de restauración."""

    source_filename: str
    output_path: Path
    total_bytes_downloaded: int
    files_extracted: int
    is_success: bool
    message: str = ""


class DownloadObserver:
    """Protocolo / Interfaz base para observar el progreso de la descarga."""

    def on_part_download_start(
        self, part_index: int, total_parts: int, filename: str, total_bytes: int
    ):
        pass

    def on_part_download_progress(self, current_bytes: int, total_bytes: int):
        pass

    def on_part_download_complete(self, part_index: int, filename: str):
        pass

    def on_extraction_start(self, total_files: int):
        pass

    def on_file_extracted(
        self, relative_path: str, size: int, current_file_idx: int, total_files: int
    ):
        pass

    def on_extraction_complete(self, total_files: int):
        pass


class NullDownloadObserver(DownloadObserver):
    """Implementación por defecto sin operaciones visuales."""

    pass


class DownloadEngine:
    """
    Motor encargado de descargar y reconstruir archivos o carpetas
    a partir de un UploadManifest (Snapshot).
    """

    def __init__(self, client: "Client", observer: Optional[DownloadObserver] = None):
        self.client = client
        self.observer = observer or NullDownloadObserver()

    def restore(
        self, manifest: UploadManifest, output_dir: Path, force: bool = False
    ) -> DownloadReport:
        """
        Punto de entrada principal para restaurar el contenido del snapshot.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        source = manifest.source

        logger.info(
            f"Iniciando restauración de '{source.filename}' hacia '{output_dir}'"
        )

        if source.type == SourceType.FOLDER:
            return self._restore_folder(manifest, output_dir, force)
        else:
            return self._restore_file(manifest, output_dir, force)

    def _download_part(
        self, part: RemotePart, destination: Path, total_parts: int = 1
    ) -> Path:
        """
        Descarga una parte física individual desde Telegram y verifica su hash MD5.
        """
        self.observer.on_part_download_start(
            part_index=part.sequence + 1,
            total_parts=total_parts,
            filename=part.part_filename,
            total_bytes=part.part_size,
        )

        msg: Optional["Message"] = self.client.get_messages(
            part.chat_id, part.message_id
        )  # type: ignore
        if not msg or getattr(msg, "empty", False) or not msg.document:
            raise FileNotFoundError(
                f"El mensaje de Telegram (Chat: {part.chat_id}, Msg: {part.message_id}) "
                f"para la pieza '{part.part_filename}' no existe o fue eliminado."
            )

        def _progress(current: int, total: int):
            self.observer.on_part_download_progress(current, total)

        destination.parent.mkdir(parents=True, exist_ok=True)

        downloaded_file = self.client.download_media(
            message=msg,
            file_name=str(destination),
            progress=_progress,
        )

        if not downloaded_file:
            raise RuntimeError(
                f"Error al descargar la pieza '{part.part_filename}' desde Telegram."
            )

        # Verificación del hash MD5 de la pieza
        computed_md5 = self._compute_file_md5(destination)
        if part.part_md5sum and computed_md5 != part.part_md5sum:
            destination.unlink(missing_ok=True)
            raise DownloadIntegrityError(
                f"Checksum MD5 incorrecto en pieza '{part.part_filename}'. "
                f"Esperado: {part.part_md5sum}, Calculado: {computed_md5}"
            )

        self.observer.on_part_download_complete(part.sequence + 1, part.part_filename)
        return destination

    def _restore_file(
        self, manifest: UploadManifest, output_dir: Path, force: bool
    ) -> DownloadReport:
        target_file = output_dir / manifest.source.filename

        if target_file.exists() and not force:
            raise FileExistsError(
                f"El archivo '{target_file.name}' ya existe en el destino. Usa --force para sobrescribirlo."
            )

        total_bytes = 0
        sorted_parts = sorted(manifest.parts, key=lambda p: p.sequence)
        total_parts = len(sorted_parts)

        with tempfile.TemporaryDirectory(prefix="totelegram_file_") as temp_dir:
            temp_path = Path(temp_dir)

            # Caso 1: Archivo de una sola parte
            if total_parts == 1:
                part = sorted_parts[0]
                temp_part = temp_path / part.part_filename
                self._download_part(part, temp_part, total_parts=1)
                total_bytes += temp_part.stat().st_size
                shutil.move(str(temp_part), str(target_file))

            # Caso 2: Archivo multipieza (Chunked)
            else:
                with open(target_file, "wb") as final_f:
                    for part in sorted_parts:
                        temp_part = temp_path / part.part_filename
                        self._download_part(part, temp_part, total_parts=total_parts)
                        total_bytes += temp_part.stat().st_size

                        with open(temp_part, "rb") as part_f:
                            shutil.copyfileobj(part_f, final_f)

                        temp_part.unlink(missing_ok=True)

        final_md5 = self._compute_file_md5(target_file)
        if manifest.source.md5sum and final_md5 != manifest.source.md5sum:
            target_file.unlink(missing_ok=True)
            raise DownloadIntegrityError(
                f"Checksum MD5 final del archivo no coincide. "
                f"Esperado: {manifest.source.md5sum}, Calculado: {final_md5}"
            )

        return DownloadReport(
            source_filename=manifest.source.filename,
            output_path=target_file,
            total_bytes_downloaded=total_bytes,
            files_extracted=1,
            is_success=True,
            message="Archivo descargado y verificado exitosamente.",
        )

    def _restore_folder(
        self, manifest: UploadManifest, output_dir: Path, force: bool
    ) -> DownloadReport:
        target_folder = output_dir / manifest.source.filename

        if target_folder.exists() and not force:
            raise FileExistsError(
                f"La carpeta '{target_folder.name}' ya existe en el destino. Usa --force para sobrescribirla."
            )

        target_folder.mkdir(parents=True, exist_ok=True)
        inventory = manifest.source.inventory or []
        sorted_parts = sorted(manifest.parts, key=lambda p: p.sequence)
        total_parts = len(sorted_parts)

        total_bytes = 0
        volume_files: Dict[int, Path] = {}

        with tempfile.TemporaryDirectory(prefix="totelegram_tape_") as temp_dir:
            temp_path = Path(temp_dir)

            for part in sorted_parts:
                vol_file = temp_path / part.part_filename
                self._download_part(part, vol_file, total_parts=total_parts)
                volume_files[part.sequence] = vol_file
                total_bytes += vol_file.stat().st_size

            total_files = len(inventory)
            self.observer.on_extraction_start(total_files)

            opened_volumes = {
                idx: open(path, "rb") for idx, path in volume_files.items()
            }

            try:
                for idx, member in enumerate(inventory, 1):
                    # Normalizar ruta para evitar duplicar el nombre de la carpeta raíz
                    rel_parts = Path(member.relative_path).parts
                    if rel_parts and rel_parts[0] == manifest.source.filename:
                        clean_rel_path = Path(*rel_parts[1:])
                    else:
                        clean_rel_path = Path(member.relative_path)

                    file_dest = target_folder / clean_rel_path
                    file_dest.parent.mkdir(parents=True, exist_ok=True)

                    # Detección inteligente para compatibilidad hacia atrás:
                    # Verifica si bytes_in_volume fue guardado como end_offset (bug previo) o como longitud neta.
                    total_if_end = sum(
                        f.bytes_in_volume - f.offset_in_vol for f in member.fragments
                    )
                    is_legacy_end_offset = total_if_end == member.size

                    hasher = hashlib.md5()
                    with open(file_dest, "wb") as out_f:
                        for frag in member.fragments:
                            vol_handle = opened_volumes.get(frag.vol_idx)
                            if not vol_handle:
                                raise ValueError(
                                    f"Volumen #{frag.vol_idx} no encontrado para {member.relative_path}"
                                )

                            vol_handle.seek(frag.offset_in_vol)

                            if is_legacy_end_offset:
                                bytes_left = frag.bytes_in_volume - frag.offset_in_vol
                            else:
                                bytes_left = frag.bytes_in_volume

                            while bytes_left > 0:
                                chunk_size = min(bytes_left, 64 * 1024)
                                chunk = vol_handle.read(chunk_size)
                                if not chunk:
                                    break
                                out_f.write(chunk)
                                hasher.update(chunk)
                                bytes_left -= len(chunk)

                    computed_md5 = hasher.hexdigest()
                    if member.md5sum and computed_md5 != member.md5sum:
                        raise DownloadIntegrityError(
                            f"Checksum MD5 inválido en archivo extraído: {member.relative_path}. "
                            f"Esperado: {member.md5sum}, Calculado: {computed_md5}"
                        )

                    self.observer.on_file_extracted(
                        relative_path=member.relative_path,
                        size=member.size,
                        current_file_idx=idx,
                        total_files=total_files,
                    )

            finally:
                for h in opened_volumes.values():
                    h.close()

            self.observer.on_extraction_complete(total_files)

        return DownloadReport(
            source_filename=manifest.source.filename,
            output_path=target_folder,
            total_bytes_downloaded=total_bytes,
            files_extracted=len(inventory),
            is_success=True,
            message="Carpeta y contenidos restaurados con integridad al 100%.",
        )

    @staticmethod
    def _compute_file_md5(file_path: Path) -> str:
        """Calcula el hash MD5 de un archivo local en bloques de 1MB."""
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()
