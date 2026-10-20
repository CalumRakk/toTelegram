import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tartape.exceptions import (
    PathConstraintError,
    TarIntegrityError,
    VolumeChecksumMismatchError,
)
from tartape.restore import reassemble_file, unpack_volumes
from tartape.schemas import FileSlice

from totelegram.packaging.schemas import RemotePart, UploadManifest
from totelegram.schemas import SourceType

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Message

logger = logging.getLogger(__name__)


class DownloadIntegrityError(Exception):
    """Raised when an extracted file or volume does not match its expected checksum or structure."""


@dataclass
class DownloadReport:
    """Consolidated summary of a restore operation."""

    source_filename: str
    output_path: Path
    total_bytes_downloaded: int
    files_extracted: int
    is_success: bool
    message: str = ""


class DownloadObserver:
    """Base interface for observing download and restore progress."""

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
    """No-op observer implementation."""


class DownloadEngine:
    """Engine responsible for downloading and reconstructing files or folders

    from an UploadManifest (Snapshot) leveraging TarTape restore primitives.
    """

    def __init__(self, client: "Client", observer: DownloadObserver | None = None):
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

        logger.info(f"Starting restoration of '{source.filename}' into '{output_dir}'")

        if source.type == SourceType.FOLDER:
            return self._restore_folder(manifest, output_dir, force, keep_cache)
        else:
            return self._restore_file(manifest, output_dir, force, keep_cache)

    def restore_single_file(
        self,
        manifest: UploadManifest,
        relative_path: str,
        output_dir: Path,
        force: bool = False,
        keep_cache: bool = False,
    ) -> DownloadReport:
        """Surgically downloads ONLY the Telegram parts containing slices of the requested

        file and reassembles it directly using tartape.restore.reassemble_file.
        """
        source = manifest.source
        if source.type != SourceType.FOLDER or not source.inventory:
            raise ValueError(
                "Surgical extraction by file is only available for archived folders (TAR tapes)."
            )

        member = next(
            (m for m in source.inventory if m.relative_path == relative_path),
            None,
        )
        if not member:
            raise FileNotFoundError(
                f"File '{relative_path}' was not found in the inventory of this snapshot."
            )

        target_file = output_dir / relative_path
        if target_file.exists() and not force:
            raise FileExistsError(
                f"File '{target_file.name}' already exists at destination. Use --force to overwrite."
            )

        target_file.parent.mkdir(parents=True, exist_ok=True)

        # Asigna fragmentos a instancias de TarTape FileSlice
        tartape_slices = [
            FileSlice(
                volume_index=frag.vol_idx,
                volume_offset=frag.volume_offset,
                volume_length=frag.volume_length,
                source_offset=frag.source_offset,
            )
            for frag in member.fragments
        ]

        # Identifica y descarga ÚNICAMENTE las partes necesarias para este archivo en concreto.
        needed_vol_indices = sorted({s.volume_index for s in tartape_slices})
        needed_parts_map = {
            p.sequence: p for p in manifest.parts if p.sequence in needed_vol_indices
        }

        staging_dir = self._get_staging_dir(output_dir, source.md5sum)
        total_parts = len(manifest.parts)
        total_bytes_transferred = 0
        downloaded_vol_paths: dict[int, Path] = {}

        logger.info(
            f"Extrayendo quirúrgicamente '{relative_path}' ({member.size} bytes). "
            f"Requiere {len(needed_vol_indices)} de {total_parts} volúmenes de Telegram."
        )

        def lazy_volume_provider(vol_idx: int) -> Path:
            """Descarga de Telegram la parte de volumen solicitada bajo demanda (JIT)."""
            nonlocal total_bytes_transferred

            if vol_idx in downloaded_vol_paths:
                return downloaded_vol_paths[vol_idx]

            part_meta = needed_parts_map.get(vol_idx)
            if not part_meta:
                raise ValueError(
                    f"Faltan metadatos para la parte de volumen {vol_idx} en el snapshot."
                )

            vol_path, bytes_dl = self._download_part(
                part=part_meta,
                staging_dir=staging_dir,
                total_parts=total_parts,
            )
            downloaded_vol_paths[vol_idx] = vol_path
            total_bytes_transferred += bytes_dl
            return vol_path

        try:
            # Delega la reconstrucción y hashing en vuelo a TarTape pasando el Callable
            reassemble_file(
                output_path=target_file,
                slices=tartape_slices,
                volumes=lazy_volume_provider,
                expected_size=member.size,
                expected_checksum=member.md5sum,
                checksum_algorithm="md5",
                overwrite=force,
            )

        except (VolumeChecksumMismatchError, TarIntegrityError) as e:
            target_file.unlink(missing_ok=True)
            raise DownloadIntegrityError(
                f"Fallo en verificación de integridad: {e}"
            ) from e
        finally:
            if not keep_cache:
                for path in downloaded_vol_paths.values():
                    path.unlink(missing_ok=True)
                if staging_dir.exists() and not any(staging_dir.iterdir()):
                    staging_dir.rmdir()

        return DownloadReport(
            source_filename=relative_path,
            output_path=target_file,
            total_bytes_downloaded=total_bytes_transferred,
            files_extracted=1,
            is_success=True,
            message=(
                f"File '{relative_path}' surgically extracted and verified "
                f"using {len(needed_vol_indices)} of {total_parts} volumes."
            ),
        )

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
        """Downloads a volume part from Telegram with fast-skip cache validation."""
        part_file = staging_dir / part.part_filename
        part_tmp = staging_dir / f"{part.part_filename}.downloading"

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
                        f"Cached segment '{part_file.name}' is corrupt. Re-downloading..."
                    )
                    part_file.unlink(missing_ok=True)
            else:
                part_file.unlink(missing_ok=True)

        part_tmp.unlink(missing_ok=True)

        self.observer.on_part_download_start(
            part_index=part.sequence + 1,
            total_parts=total_parts,
            filename=part.part_filename,
            total_bytes=part.part_size,
        )

        msg: "Message" | None = self.client.get_messages(part.chat_id, part.message_id)  # type: ignore  # noqa: UP037
        if not msg or getattr(msg, "empty", False) or not msg.document:
            raise FileNotFoundError(
                f"Telegram message (Chat: {part.chat_id}, Msg: {part.message_id}) "
                f"for part '{part.part_filename}' does not exist or was deleted."
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
                f"Failed to download piece '{part.part_filename}' from Telegram."
            )

        computed_md5 = self._compute_file_md5(part_tmp)
        if part.part_md5sum and computed_md5 != part.part_md5sum:
            part_tmp.unlink(missing_ok=True)
            raise DownloadIntegrityError(
                f"Invalid MD5 checksum for downloaded part '{part.part_filename}'. "
                f"Expected: {part.part_md5sum}, Computed: {computed_md5}"
            )

        part_tmp.replace(part_file)
        self.observer.on_part_download_complete(part.sequence + 1, part.part_filename)
        return part_file, part_file.stat().st_size

    def _restore_file(
        self,
        manifest: UploadManifest,
        output_dir: Path,
        force: bool,
        keep_cache: bool,
    ) -> DownloadReport:
        """Restores a monolithic or split regular file."""
        target_file = output_dir / manifest.source.filename

        if target_file.exists() and not force:
            raise FileExistsError(
                f"File '{target_file.name}' already exists at destination. Use --force to overwrite."
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
            target_file.unlink(missing_ok=True)
            downloaded_paths[0].replace(target_file)
        else:
            assembling_file = output_dir / f"{manifest.source.filename}.assembling"
            assembling_file.unlink(missing_ok=True)

            with open(assembling_file, "wb") as final_f:
                for part_path in downloaded_paths:
                    with open(part_path, "rb") as part_f:
                        while chunk := part_f.read(64 * 1024):
                            final_f.write(chunk)

            assembling_file.replace(target_file)

        final_md5 = self._compute_file_md5(target_file)
        if manifest.source.md5sum and final_md5 != manifest.source.md5sum:
            target_file.unlink(missing_ok=True)
            raise DownloadIntegrityError(
                f"Final file MD5 checksum mismatch. "
                f"Expected: {manifest.source.md5sum}, Computed: {final_md5}"
            )

        if not keep_cache:
            for p in downloaded_paths:
                p.unlink(missing_ok=True)
            if staging_dir.exists() and not any(staging_dir.iterdir()):
                staging_dir.rmdir()

        return DownloadReport(
            source_filename=manifest.source.filename,
            output_path=target_file,
            total_bytes_downloaded=total_bytes_transferred,
            files_extracted=1,
            is_success=True,
            message="Regular file downloaded, reassembled, and verified successfully.",
        )

    def _restore_folder(
        self,
        manifest: UploadManifest,
        output_dir: Path,
        force: bool,
        keep_cache: bool,
    ) -> DownloadReport:
        """Restaura una carpeta archivada extrayendo los volúmenes de forma secuencial en cascada."""
        target_folder = output_dir / manifest.source.filename

        if target_folder.exists() and not force:
            raise FileExistsError(
                f"La carpeta '{target_folder.name}' ya existe en el destino. Usa --force para sobrescribir."
            )

        staging_dir = self._get_staging_dir(output_dir, manifest.source.md5sum)
        sorted_parts = sorted(manifest.parts, key=lambda p: p.sequence)
        total_parts = len(sorted_parts)
        total_bytes_transferred = 0

        # Preparar observador y callbacks de extracción
        inventory = manifest.source.inventory or []
        total_expected_files = len(inventory) if inventory else 0
        self.observer.on_extraction_start(total_expected_files)

        extracted_files_count = 0

        def on_extracted(arc_name: str, size: int):
            nonlocal extracted_files_count
            extracted_files_count += 1
            self.observer.on_file_extracted(
                relative_path=arc_name,
                size=size,
                current_file_idx=extracted_files_count,
                total_files=total_expected_files or extracted_files_count,
            )

        # Generador perezoso que descarga y abre cada volumen secuencialmente
        def volume_stream_generator():
            nonlocal total_bytes_transferred
            previous_vol_file: Path | None = None

            for part in sorted_parts:
                # Si no se desea conservar la caché, liberar el volumen anterior antes de descargar el siguiente
                if not keep_cache and previous_vol_file and previous_vol_file.exists():
                    try:
                        previous_vol_file.unlink()
                    except Exception:  # noqa: S110
                        pass

                vol_file, bytes_dl = self._download_part(
                    part, staging_dir, total_parts=total_parts
                )
                total_bytes_transferred += bytes_dl
                previous_vol_file = vol_file

                # Abre el archivo para lectura; ChainedVolumeStream se encargará de cerrarlo al agotarlo
                f = open(vol_file, "rb")  # noqa: SIM115
                yield f

            # Limpieza del último archivo al finalizar el generador
            if not keep_cache and previous_vol_file and previous_vol_file.exists():
                try:
                    previous_vol_file.unlink()
                except Exception:  # noqa: S110
                    pass

        try:
            unpack_volumes(
                volume_streams=volume_stream_generator(),
                dest_dir=output_dir,
                on_file_extracted=on_extracted,
                overwrite=force,
            )
        except PathConstraintError as e:
            raise DownloadIntegrityError(
                f"Violación de seguridad al extraer TAR: {e}"
            ) from e
        except Exception as e:
            raise DownloadIntegrityError(
                f"Error al desempaquetar archivo TAR: {e}"
            ) from e
        finally:
            if (
                not keep_cache
                and staging_dir.exists()
                and not any(staging_dir.iterdir())
            ):
                staging_dir.rmdir()

        self.observer.on_extraction_complete(extracted_files_count)

        # Validación opcional de hashes de archivos extraídos contra el inventario
        if inventory:
            for member in inventory:
                if not member.md5sum:
                    continue
                extracted_file = output_dir / member.relative_path
                if extracted_file.is_file():
                    computed_md5 = self._compute_file_md5(extracted_file)
                    if computed_md5 != member.md5sum:
                        raise DownloadIntegrityError(
                            f"Discrepancia de checksum para archivo extraído '{member.relative_path}'. "
                            f"Esperado: {member.md5sum}, Calculado: {computed_md5}"
                        )

        return DownloadReport(
            source_filename=manifest.source.filename,
            output_path=target_folder,
            total_bytes_downloaded=total_bytes_transferred,
            files_extracted=extracted_files_count,
            is_success=True,
            message="Carpeta y contenidos restaurados con integridad 100% verificada.",
        )

    @staticmethod
    def _compute_file_md5(file_path: Path) -> str:
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
