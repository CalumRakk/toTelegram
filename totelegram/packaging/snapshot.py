import json
import logging
import lzma
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import tartape

from totelegram.models import Job, Payload, RemotePayload
from totelegram.packaging.schemas import (
    TTAPE_EXTENSION,
    RemotePart,
    SourceMetadata,
    UploadManifest,
)
from totelegram.packaging.ttape import TapeContainer
from totelegram.schemas import SourceType

if TYPE_CHECKING:
    from pyrogram.client import Client

logger = logging.getLogger(__name__)


class SnapshotService:
    @staticmethod
    def load_snapshot(path: Path, client: Optional["Client"] = None) -> UploadManifest:
        """Carga y valida un snapshot de forma polimórfica (.ttape o .json.xz)."""
        target = Path(path).resolve()
        if not target.exists():
            raise FileNotFoundError(f"Snapshot no encontrado en: {target}")

        if target.suffix == TTAPE_EXTENSION or target.name.endswith(TTAPE_EXTENSION):
            return TapeContainer.read_manifest(target)

        # Para formatos antiguos o .json.xz, delegamos en el upgrader
        from totelegram.packaging.upgrader import SnapshotUpgrader

        return SnapshotUpgrader.load_and_upgrade(target, tg_client=client)

    @staticmethod
    def generate_snapshot(job: Job) -> UploadManifest:
        """Genera el snapshot en disco bifurcando entre .json.xz (archivo) y .ttape (carpeta)."""
        source = job.source
        original_file_path = Path(source.path_str)

        # 1. Recuperar piezas remotas activas
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

        # 2. Metadatos del Source (en v6.0 el inventario JSON es None)
        source_meta = SourceMetadata(
            filename=original_file_path.name,
            size=source.size,
            md5sum=source.md5sum,
            mime_type=source.mimetype,
            mtime=source.mtime,
            type=source.type,
            tape_catalog=source.tape_catalog,
            inventory=None,
        )

        manifest = UploadManifest(
            strategy=job.strategy,
            chunk_size=job.config.tg_max_size,
            created_at=job.created_at,
            chat_id=job.chat.id,
            owner_id=owner.id,
            owner_name=owner.first_name,
            source=source_meta,
            parts=parts,
        )

        # 3. Bifurcación física de almacenamiento
        output_path = SnapshotService._resolve_snapshot_path(
            original_file_path, source.md5sum, source.type
        )

        if source.type == SourceType.FOLDER:
            # Localizar el catálogo SQLite generado por tartape en T0
            catalog_file = tartape.discover(original_file_path)
            if not catalog_file or not catalog_file.exists():
                raise FileNotFoundError(
                    f"No se encontró el catálogo .tartape para la carpeta '{original_file_path.name}'."
                )

            # Construir contenedor híbrido .ttape (ZIP: manifest.json + catalog.tartape)
            TapeContainer.build(
                output_path=output_path,
                manifest=manifest,
                catalog_path=catalog_file,
                overwrite=True,
            )

            # Cero residuos en disco: se absorbe y se elimina el .tartape suelto
            try:
                catalog_file.unlink(missing_ok=True)
                logger.debug(
                    f"Catálogo residual eliminado tras absorción: {catalog_file.name}"
                )
            except OSError as e:
                logger.warning(
                    f"No se pudo eliminar el catálogo temporal {catalog_file}: {e}"
                )

        else:
            # Archivo individual: formato ligero .json.xz
            with lzma.open(output_path, "wt", encoding="utf-8") as f:
                f.write(manifest.model_dump_json(indent=2))

        logger.info(f"Snapshot generado exitosamente: {output_path.name}")
        return manifest

    @staticmethod
    def _resolve_snapshot_path(
        file_path: Path, current_md5: str, source_type: SourceType
    ) -> Path:
        """Resuelve la ruta final del snapshot evitando colisiones y asignando la extensión correcta."""
        extension = TTAPE_EXTENSION if source_type == SourceType.FOLDER else ".json.xz"
        base_target = file_path.with_name(f"{file_path.name}{extension}")

        if not base_target.exists():
            return base_target

        # Si ya existe y es del mismo MD5, reutilizamos la misma ruta
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

        # Generador de nombres incrementales si hay colisión
        counter = 1
        while True:
            new_target = file_path.with_name(
                f"{file_path.stem} ({counter}){file_path.suffix}{extension}"
            )
            if not new_target.exists():
                return new_target
            counter += 1
