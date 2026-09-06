import json
import logging
import lzma
from pathlib import Path
from typing import List, Optional

from totelegram.models import Job, Payload, RemotePayload, TapeMember, TapeMemberGPS
from totelegram.packaging.schemas import (
    FileFragment,
    RemotePart,
    SourceMetadata,
    TapeMemberSnapshot,
    UploadManifest,
)
from totelegram.schemas import SourceType

logger = logging.getLogger(__name__)


class SnapshotService:
    @staticmethod
    def load_snapshot(snapshot_path: Path) -> UploadManifest:
        """
        Carga, descomprime y valida un archivo snapshot (.json.xz).
        Lanza FileNotFoundError o ValueError si el archivo no existe o está corrupto.
        """
        if not snapshot_path.exists():
            raise FileNotFoundError(f"El archivo snapshot no existe: {snapshot_path}")

        try:
            with lzma.open(snapshot_path, "rt", encoding="utf-8") as f:
                raw_json = f.read()
            manifest = UploadManifest.model_validate_json(raw_json)
            logger.info(
                f"Snapshot cargado correctamente: {manifest.source.filename} "
                f"(Estrategia: {manifest.strategy}, Partes: {len(manifest.parts)})"
            )
            return manifest
        except Exception as e:
            logger.error(f"Error al deserializar snapshot {snapshot_path.name}: {e}")
            raise ValueError(
                f"El archivo snapshot '{snapshot_path.name}' es inválido o está corrupto: {e}"
            ) from e

    @staticmethod
    def generate_snapshot(job: Job) -> UploadManifest:
        source = job.source
        original_file_path = Path(source.path_str)

        # Recuperar remotos existentes y activos uniendo con Payload
        remotes_db = list(
            RemotePayload.select(RemotePayload, Payload)
            .join(Payload)
            .where(
                (Payload.job == job) & (RemotePayload.is_orphaned == False)  # noqa: E712
            )
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

        inventory: Optional[List[TapeMemberSnapshot]] = None
        if source.type == SourceType.FOLDER:
            inventory = []
            # Prefetch correcto usando el modelo Payload
            members = (
                TapeMember.select()
                .where(TapeMember.source == source)
                .prefetch(TapeMemberGPS, Payload)
            )

            for m in members:
                fragments = [
                    FileFragment(
                        vol_idx=gps.payload.sequence_index,
                        offset_in_vol=gps.offset_in_volume,
                        bytes_in_volume=gps.bytes_in_volume,
                    )
                    for gps in m.fragments
                ]

                inventory.append(
                    TapeMemberSnapshot(
                        relative_path=m.relative_path,
                        size=m.size,
                        md5sum=m.md5sum,
                        fragments=fragments,
                    )
                )

        owner = remotes_db[0].owner

        # Mapeamos a los metadatos independientes de la base de datos
        source_meta = SourceMetadata(
            filename=original_file_path.name,
            size=source.size,
            md5sum=source.md5sum,
            mime_type=source.mimetype,
            mtime=source.mtime,
            type=source.type,
            tape_catalog=source.tape_catalog,
            inventory=inventory,
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

        # Escritura física y atómica en disco
        output_path = SnapshotService._resolve_snapshot_path(
            original_file_path, source.md5sum
        )

        with lzma.open(output_path, "wt", encoding="utf-8") as f:
            f.write(manifest.model_dump_json(indent=2))

        return manifest

    @staticmethod
    def _resolve_snapshot_path(file_path: Path, current_md5: str) -> Path:
        base_target = file_path.with_name(f"{file_path.name}.json.xz")

        if not base_target.exists():
            return base_target

        try:
            with lzma.open(base_target, "rt", encoding="utf-8") as f:
                existing_data = json.load(f)
                if existing_data.get("source", {}).get("md5sum") == current_md5:
                    return base_target
        except Exception:
            pass

        counter = 1
        while True:
            new_target = file_path.with_name(
                f"{file_path.stem} ({counter}){file_path.suffix}.json.xz"
            )
            if not new_target.exists():
                return new_target
            counter += 1
