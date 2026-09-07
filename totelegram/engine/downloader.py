import hashlib
import io
import logging
import os
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

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

    def on_part_skipped(self, part_index: int, total_parts: int, filename: str):
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


class ChainedStream(io.RawIOBase):
    """
    Flujo de lectura secuencial que concatena múltiples archivos de volúmenes en disco
    como si fueran un único flujo TAR continuo, sin cargarlos completos en memoria RAM.
    """

    def __init__(self, paths: List[Path]):
        self.paths = paths
        self._idx = 0
        self._current_file: Optional[io.BufferedReader] = None
        self._open_next()

    def _open_next(self):
        if self._current_file:
            self._current_file.close()
            self._current_file = None
        if self._idx < len(self.paths):
            self._current_file = open(self.paths[self._idx], "rb")
            self._idx += 1

    def readinto(self, b) -> int:
        if not self._current_file:
            return 0
        n = self._current_file.readinto(b)
        if n == 0 or n is None:
            self._open_next()
            if not self._current_file:
                return 0
            return self._current_file.readinto(b) or 0
        return n

    def readable(self) -> bool:
        return True

    def close(self):
        if self._current_file:
            self._current_file.close()
            self._current_file = None
        super().close()


class DownloadEngine:
    """
    Motor encargado de descargar y reconstruir archivos o carpetas
    a partir de un UploadManifest (Snapshot) usando staging local persistente.
    """

    def __init__(self, client: "Client", observer: Optional[DownloadObserver] = None):
        self.client = client
        self.observer = observer or NullDownloadObserver()

    def restore(
        self,
        manifest: UploadManifest,
        output_dir: Path,
        force: bool = False,
        keep_cache: bool = False,
    ) -> DownloadReport:
        output_dir.mkdir(parents=True, exist_ok=True)
        source = manifest.source

        logger.info(
            f"Iniciando restauración de '{source.filename}' hacia '{output_dir}'"
        )

        if source.type == SourceType.FOLDER:
            return self._restore_folder(manifest, output_dir, force, keep_cache)
        else:
            return self._restore_file(manifest, output_dir, force, keep_cache)

    def _get_staging_dir(self, output_dir: Path, source_md5: str) -> Path:
        short_hash = source_md5[:12] if source_md5 else "temp"
        staging = output_dir / f".totelegram_cache_{short_hash}"
        staging.mkdir(parents=True, exist_ok=True)
        return staging

    def _download_part(
        self,
        part: RemotePart,
        staging_dir: Path,
        total_parts: int = 1,
    ) -> tuple[Path, int]:
        part_file = staging_dir / part.part_filename
        part_tmp = staging_dir / f"{part.part_filename}.downloading"

        # Comprobación rápida de existencia e integridad (Fast-Skip)
        if part_file.exists():
            if part_file.stat().st_size == part.part_size:
                computed_md5 = self._compute_file_md5(part_file)
                if not part.part_md5sum or computed_md5 == part.part_md5sum:
                    self.observer.on_part_skipped(
                        part_index=part.sequence + 1,
                        total_parts=total_parts,
                        filename=part.part_filename,
                    )
                    return part_file, 0
                else:
                    logger.warning(
                        f"Segmento '{part_file.name}' corrupto en disco. Re-descargando..."
                    )
                    part_file.unlink(missing_ok=True)
            else:
                part_file.unlink(missing_ok=True)

        # Descarga a archivo temporal
        part_tmp.unlink(missing_ok=True)

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

        downloaded_file = self.client.download_media(
            message=msg,
            file_name=str(part_tmp),
            progress=_progress,
        )

        if not downloaded_file:
            raise RuntimeError(
                f"Error al descargar la pieza '{part.part_filename}' desde Telegram."
            )

        # Verificación de integridad de la pieza descargada
        computed_md5 = self._compute_file_md5(part_tmp)
        if part.part_md5sum and computed_md5 != part.part_md5sum:
            part_tmp.unlink(missing_ok=True)
            raise DownloadIntegrityError(
                f"Checksum MD5 incorrecto en pieza '{part.part_filename}'. "
                f"Esperado: {part.part_md5sum}, Calculado: {computed_md5}"
            )

        # Renombrado atómico
        shutil.move(str(part_tmp), str(part_file))

        self.observer.on_part_download_complete(part.sequence + 1, part.part_filename)
        return part_file, part_file.stat().st_size

    def _restore_file(
        self,
        manifest: UploadManifest,
        output_dir: Path,
        force: bool,
        keep_cache: bool,
    ) -> DownloadReport:
        target_file = output_dir / manifest.source.filename

        if target_file.exists() and not force:
            raise FileExistsError(
                f"El archivo '{target_file.name}' ya existe en el destino. Usa --force para sobrescribirlo."
            )

        staging_dir = self._get_staging_dir(output_dir, manifest.source.md5sum)
        total_bytes_transferred = 0
        sorted_parts = sorted(manifest.parts, key=lambda p: p.sequence)
        total_parts = len(sorted_parts)

        downloaded_paths = []
        for part in sorted_parts:
            part_path, bytes_dl = self._download_part(
                part, staging_dir, total_parts=total_parts
            )
            downloaded_paths.append(part_path)
            total_bytes_transferred += bytes_dl

        if total_parts == 1:
            shutil.copy2(str(downloaded_paths[0]), str(target_file))
        else:
            assembling_file = output_dir / f"{manifest.source.filename}.assembling"
            assembling_file.unlink(missing_ok=True)

            with open(assembling_file, "wb") as final_f:
                for part_path in downloaded_paths:
                    with open(part_path, "rb") as part_f:
                        shutil.copyfileobj(part_f, final_f)

            assembling_file.replace(target_file)

        final_md5 = self._compute_file_md5(target_file)
        if manifest.source.md5sum and final_md5 != manifest.source.md5sum:
            target_file.unlink(missing_ok=True)
            raise DownloadIntegrityError(
                f"Checksum MD5 final del archivo no coincide. "
                f"Esperado: {manifest.source.md5sum}, Calculado: {final_md5}"
            )

        if not keep_cache:
            shutil.rmtree(staging_dir, ignore_errors=True)

        return DownloadReport(
            source_filename=manifest.source.filename,
            output_path=target_file,
            total_bytes_downloaded=total_bytes_transferred,
            files_extracted=1,
            is_success=True,
            message="Archivo descargado, ensamblado y verificado exitosamente.",
        )

    def _restore_folder(
        self,
        manifest: UploadManifest,
        output_dir: Path,
        force: bool,
        keep_cache: bool,
    ) -> DownloadReport:
        target_folder = output_dir / manifest.source.filename

        if target_folder.exists() and not force:
            raise FileExistsError(
                f"La carpeta '{target_folder.name}' ya existe en el destino. Usa --force para sobrescribirla."
            )

        staging_dir = self._get_staging_dir(output_dir, manifest.source.md5sum)

        sorted_parts = sorted(manifest.parts, key=lambda p: p.sequence)
        total_parts = len(sorted_parts)

        total_bytes_transferred = 0
        volume_paths: List[Path] = []

        # Descargar o reutilizar volúmenes
        for part in sorted_parts:
            vol_file, bytes_dl = self._download_part(
                part, staging_dir, total_parts=total_parts
            )
            volume_paths.append(vol_file)
            total_bytes_transferred += bytes_dl

        # Extracción mediante flujo TAR streaming
        inventory = manifest.source.inventory or []
        md5_map = {m.relative_path: m.md5sum for m in inventory if m.md5sum}
        total_expected_files = len(inventory) if inventory else 0

        self.observer.on_extraction_start(total_expected_files)

        resolved_output_dir = output_dir.resolve()
        chained_stream = ChainedStream(volume_paths)
        buffered_stream = io.BufferedReader(chained_stream)
        extracted_files_count = 0

        try:
            with tarfile.open(fileobj=buffered_stream, mode="r|*") as tar:
                for tarinfo in tar:
                    dest_path = (output_dir / tarinfo.name).resolve()

                    # Protección contra path traversal
                    if not str(dest_path).startswith(str(resolved_output_dir)):
                        raise DownloadIntegrityError(
                            f"Ruta maliciosa o fuera de destino detectada en TAR: {tarinfo.name}"
                        )

                    if tarinfo.isdir():
                        dest_path.mkdir(parents=True, exist_ok=True)
                        continue

                    if tarinfo.issym():
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        if dest_path.is_symlink() or dest_path.exists():
                            dest_path.unlink()
                        dest_path.symlink_to(tarinfo.linkname)
                        continue

                    if tarinfo.isreg():
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        hasher = hashlib.md5()
                        fileobj = tar.extractfile(tarinfo)
                        if fileobj is None:
                            continue

                        with open(dest_path, "wb") as out_f:
                            while chunk := fileobj.read(64 * 1024):
                                out_f.write(chunk)
                                hasher.update(chunk)

                        try:
                            os.utime(dest_path, (tarinfo.mtime, tarinfo.mtime))
                            os.chmod(dest_path, tarinfo.mode)
                        except OSError:
                            pass

                        computed_md5 = hasher.hexdigest()
                        expected_md5 = md5_map.get(tarinfo.name)
                        if expected_md5 and computed_md5 != expected_md5:
                            raise DownloadIntegrityError(
                                f"Checksum MD5 inválido en archivo extraído: {tarinfo.name}. "
                                f"Esperado: {expected_md5}, Calculado: {computed_md5}"
                            )

                        extracted_files_count += 1
                        self.observer.on_file_extracted(
                            relative_path=tarinfo.name,
                            size=tarinfo.size,
                            current_file_idx=extracted_files_count,
                            total_files=total_expected_files or extracted_files_count,
                        )

        finally:
            buffered_stream.close()
            chained_stream.close()

        self.observer.on_extraction_complete(extracted_files_count)

        # Limpieza de staging tras éxito total
        if not keep_cache:
            shutil.rmtree(staging_dir, ignore_errors=True)

        return DownloadReport(
            source_filename=manifest.source.filename,
            output_path=target_folder,
            total_bytes_downloaded=total_bytes_transferred,
            files_extracted=extracted_files_count,
            is_success=True,
            message="Carpeta y contenidos restaurados con integridad al 100%.",
        )

    @staticmethod
    def _compute_file_md5(file_path: Path) -> str:
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()
