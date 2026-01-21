import json
import logging
import lzma
from pathlib import Path

from totelegram.core.schemas import (
    MANIFEST_VERSION,
    RemotePart,
    SourceMetadata,
    UploadManifest,
)
from totelegram.store.models import Job, Payload, RemotePayload
from totelegram.telegram import parse_message_json_data

logger = logging.getLogger(__name__)


class SnapshotService:
    @staticmethod
    def generate_snapshot(job: Job) -> UploadManifest:
        """
        Genera el manifiesto (snapshot).
        Si el path ya existe pero pertenece a otro MD5, genera un nombre nuevo (ej: archivo (1).json.xz).
        """
        source = job.source
        original_file_path = Path(source.path_str)

        first_remote = (
            RemotePayload.select().join(Payload).where(Payload.job == job).first()
        )

        if not first_remote:
            raise ValueError(
                f"No se puede generar snapshot: El Job {job.id} no tiene registros remotos en la DB."
            )

        owner_id = first_remote.owner.id
        owner_name = first_remote.owner.first_name

        # Resolver el path del snapshot evitando colisiones
        output_path = SnapshotService._resolve_snapshot_path(
            original_file_path, source.md5sum
        )

        logger.info(
            f"Generando manifiesto {MANIFEST_VERSION} para Job {job.id} -> {output_path.name}"
        )

        # Mapear Partes Remotas
        parts: list[RemotePart] = []
        payloads = job.payloads.order_by(Payload.sequence_index)

        for payload in payloads:
            remote_part_db = RemotePayload.get_or_none(RemotePayload.payload == payload)

            if not remote_part_db:
                logger.warning(
                    f"Payload {payload.sequence_index} no tiene registro remoto. Snapshot incompleto."
                )
                continue

            # Reconstruir mensaje para obtener el link
            message = parse_message_json_data(remote_part_db.json_metadata)

            parts.append(
                RemotePart(
                    sequence=payload.sequence_index,
                    message_id=remote_part_db.message_id,
                    chat_id=remote_part_db.chat_id,
                    link=message.link or "",
                    part_filename=Path(payload.temp_path).name,
                    part_size=payload.size,
                    part_md5sum=payload.md5sum,
                )
            )

        # Crear Manifiesto
        manifest = UploadManifest(
            app_version=job.config.app_version,
            strategy=job.strategy,
            chunk_size=job.config.tg_max_size,
            created_at=job.created_at,
            target_chat_id=job.chat.id,
            owner_id=owner_id,
            owner_name=owner_name,
            source=SourceMetadata(
                filename=original_file_path.name,
                size=source.size,
                md5sum=source.md5sum,
                mime_type=source.mimetype,
                mtime=source.mtime,
            ),
            parts=parts,
        )
        # Guardado atómico con compresión LZMA
        with lzma.open(output_path, "wt", encoding="utf-8") as f:
            f.write(manifest.model_dump_json(indent=2))

        return manifest

    @staticmethod
    def _resolve_snapshot_path(file_path: Path, current_md5: str) -> Path:
        """
        Busca un nombre de archivo disponible.
        Si el archivo .json.xz ya existe:
        - Si es del mismo MD5: Se sobrescribe (es una actualización).
        - Si es de otro MD5: Se busca nombre (1), (2), etc.
        """
        # El nombre base será: nombre_archivo.ext.json.xz (mantenemos la ext original en el nombre para claridad)
        base_target = file_path.with_name(f"{file_path.name}.json.xz")

        if not base_target.exists():
            return base_target

        try:
            with lzma.open(base_target, "rt", encoding="utf-8") as f:
                existing_data = json.load(f)
                existing_md5 = existing_data.get("source", {}).get("md5sum")
                if existing_md5 == current_md5:
                    return base_target
        except Exception:
            # Si el archivo está corrupto o no se puede leer, asumimos que debemos numerar
            pass

        counter = 1
        while True:
            new_name = f"{file_path.stem} ({counter}){file_path.suffix}.json.xz"
            new_target = file_path.with_name(new_name)
            if not new_target.exists():
                return new_target

            # Verificamos el MD5 de los numerados por si acaso
            try:
                with lzma.open(new_target, "rt", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    if existing_data.get("source", {}).get("md5sum") == current_md5:
                        return new_target
            except Exception:
                pass

            counter += 1
