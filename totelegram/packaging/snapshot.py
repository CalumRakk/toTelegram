import json
import logging
import lzma
from pathlib import Path

import tartape

from totelegram.models import Job, Payload, RemotePayload
from totelegram.packaging.schemas import (
    MANIFEST_VERSION,
    TTAPE_EXTENSION,
    RemotePart,
    SourceMetadata,
    UploadManifest,
)
from totelegram.packaging.ttape import TapeContainer
from totelegram.schemas import SourceType

logger = logging.getLogger(__name__)


class SnapshotService:
    """Servicio de generación, resolución y carga de Snapshots modernos v6.0."""

    @staticmethod
    def load_snapshot(path: Path) -> UploadManifest:
        """Carga y valida un snapshot de forma polimórfica (.ttape o .json.xz v6.0).

        Args:
            path: Ruta al archivo snapshot.

        Returns:
            UploadManifest: Manifiesto validado en esquema v6.0.

        Raises:
            FileNotFoundError: Si el archivo no existe.
            ValueError: Si el formato o versión no corresponden a v6.0.
        """
        target = Path(path).resolve()
        if not target.exists():
            raise FileNotFoundError(f"Snapshot no encontrado en: {target}")

        # Snapshot .ttape
        if target.suffix == TTAPE_EXTENSION or target.name.endswith(TTAPE_EXTENSION):
            manifest = TapeContainer.read_manifest(target)
            if manifest.manifest_version != MANIFEST_VERSION:
                raise ValueError(
                    f"Versión de contenedor .ttape no soportada ({manifest.manifest_version}). "
                    f"Se requiere {MANIFEST_VERSION}."
                )
            return manifest

        # Snapshot .json.xz
        if target.suffix == ".xz" or target.name.endswith(".json.xz"):
            try:
                with lzma.open(target, "rt", encoding="utf-8") as f:
                    raw_data = json.load(f)
                    manifest = UploadManifest.model_validate(raw_data)
                    if manifest.manifest_version != MANIFEST_VERSION:
                        raise ValueError(
                            f"Versión no soportada ({manifest.manifest_version}). "
                            f"Se requiere {MANIFEST_VERSION}."
                        )
                    return manifest
            except Exception as e:
                raise ValueError(
                    f"Error al leer o validar el snapshot '{target.name}': {e}"
                ) from e

        raise ValueError(
            f"Formato no soportado para '{target.name}'. "
            f"Solo se admiten archivos .ttape y .json.xz v6.0."
        )

    @staticmethod
    def generate_snapshot(
        job: Job, staging_dir: Path | None = None
    ) -> tuple[UploadManifest, Path]:
        """Genera el snapshot en disco bifurcando entre .json.xz (archivo) y .ttape (carpeta).

        Si se especifica `staging_dir`, el archivo se genera en ese directorio temporal
        para evitar contaminar la carpeta del usuario.

        Retorna una tupla (UploadManifest, Path del archivo generado).
        """
        source = job.source
        original_file_path = Path(source.path_str)

        # Recuperar piezas remotas activas
        remotes_db = list(
            RemotePayload.select(RemotePayload, Payload)
            .join(Payload)
            .where((Payload.job == job) & (RemotePayload.is_orphaned == False))
            .order_by(Payload.sequence_index)
        )

        if not remotes_db:
            raise ValueError(
                f"No hay registros remotos para el Job {job.id}. Imposible crear snapshot."
            )

        parts = [
            RemotePart(
                sequence=r.payload.sequence_index,
                message_id=r.message_id,
                chat_id=r.chat_id,
                link=r.message.link or "",
                part_filename=r.payload.filename,
                part_size=r.payload.size,
                part_md5sum=r.payload.md5sum or "",
                start_offset=r.payload.start_offset,
                end_offset=r.payload.end_offset,
            )
            for r in remotes_db
        ]

        owner = remotes_db[0].owner

        # Metadatos del Source
        source_meta = SourceMetadata(
            filename=original_file_path.name,
            size=source.size,
            md5sum=source.md5sum,
            mime_type=source.mimetype,
            mtime=source.mtime,
            type=source.type,
            tape_catalog=source.tape_catalog,
        )

        manifest = UploadManifest(
            chunk_size=job.chunk_size,
            created_at=job.created_at,
            chat_id=job.chat.id,
            owner_id=owner.id,
            owner_name=owner.first_name,
            source=source_meta,
            parts=parts,
        )

        # Bifurcación física de almacenamiento: Staging o Directorio Local
        extension = TTAPE_EXTENSION if source.type == SourceType.FOLDER else ".json.xz"

        if staging_dir is not None:
            output_path = Path(staging_dir) / f"{original_file_path.name}{extension}"
        else:
            output_path = SnapshotService.resolve_snapshot_path(
                original_file_path, source.md5sum, source.type
            )

        if source.type == SourceType.FOLDER:
            catalog_file = tartape.discover(original_file_path)
            if not catalog_file or not catalog_file.exists():
                raise FileNotFoundError(
                    f"No se encontró el catálogo .tartape para la carpeta '{original_file_path.name}'."
                )

            TapeContainer.build(
                output_path=output_path,
                manifest=manifest,
                catalog_path=catalog_file,
                overwrite=True,
            )

            try:
                catalog_file.unlink(missing_ok=True)
            except OSError as e:
                logger.warning(
                    f"No se pudo eliminar el catálogo temporal {catalog_file}: {e}"
                )

        else:
            with lzma.open(output_path, "wt", encoding="utf-8") as f:
                f.write(manifest.model_dump_json(indent=2))

        logger.info(f"Snapshot generado exitosamente: {output_path.name}")
        return manifest, output_path

    @staticmethod
    def resolve_snapshot_path(
        file_path: Path, current_md5: str, source_type: SourceType
    ) -> Path:
        """Resuelve la ruta final del snapshot evitando colisiones y asignando la extensión correcta."""
        extension = TTAPE_EXTENSION if source_type == SourceType.FOLDER else ".json.xz"
        base_target = file_path.with_name(f"{file_path.name}{extension}")

        if not base_target.exists():
            return base_target

        try:
            if source_type == SourceType.FOLDER:
                existing_manifest = TapeContainer.read_manifest(base_target)
                if existing_manifest.source.md5sum == current_md5:
                    return base_target
            else:
                with lzma.open(base_target, "rt", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    if existing_data.get("source", {}).get("md5sum") == current_md5:
                        return base_target
        except Exception:  # noqa: S110
            pass

        counter = 1
        while True:
            new_target = file_path.with_name(
                f"{file_path.stem} ({counter}){file_path.suffix}{extension}"
            )
            if not new_target.exists():
                return new_target
            counter += 1
