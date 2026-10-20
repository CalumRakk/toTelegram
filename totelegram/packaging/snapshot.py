import json
import logging
import lzma
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from totelegram.models import Job, Payload, RemotePayload, TapeMember
from totelegram.packaging.schemas import (
    FileFragment,
    RemotePart,
    SourceMetadata,
    TapeMemberSnapshot,
    UploadManifest,
)
from totelegram.schemas import SourceType

if TYPE_CHECKING:
    from pyrogram.client import Client


logger = logging.getLogger(__name__)


class SnapshotService:
    @staticmethod
    def load_snapshot(path: Path, client: Optional["Client"] = None) -> UploadManifest:
        """Carga y valida un snapshot, auto-migrándolo transparentemente a v5.0 si es antiguo."""
        from totelegram.packaging.upgrader import SnapshotUpgrader

        return SnapshotUpgrader.load_and_upgrade(path, tg_client=client)

    @staticmethod
    def generate_snapshot(job: Job) -> UploadManifest:
        source = job.source
        original_file_path = Path(source.path_str)

        # Recuperar remotos existentes y activos uniendo con Payload
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

        inventory: List[TapeMemberSnapshot] | None = None
        if source.type == SourceType.FOLDER:
            inventory = []

            # Obtenemos los miembros ordenados por su posición en el TAR continuo
            members = (
                TapeMember.select()
                .where(TapeMember.source == source)
                .order_by(TapeMember.start_offset)
            )

            chunk_size = job.config.tg_max_size
            for m in members:
                # Calculamos los fragmentos quirúrgicos en memoria vía O(1)
                fragments = [
                    FileFragment(
                        vol_idx=sl.volume_index,
                        volume_offset=sl.volume_offset,
                        volume_length=sl.volume_length,
                        source_offset=sl.source_offset,
                    )
                    for sl in m.get_slices(chunk_size)
                ]

                inventory.append(
                    TapeMemberSnapshot(
                        relative_path=m.relative_path,
                        size=m.size,
                        md5sum=m.md5sum,
                        start_offset=m.start_offset,
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
        except Exception:  # noqa: S110
            pass

        counter = 1
        while True:
            new_target = file_path.with_name(
                f"{file_path.stem} ({counter}){file_path.suffix}.json.xz"
            )
            if not new_target.exists():
                return new_target
            counter += 1
