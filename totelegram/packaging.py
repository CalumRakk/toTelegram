import logging
import lzma
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, cast

from pydantic import BaseModel

from totelegram import __version__
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TapeMember,
    TapeMemberGPS,
)
from totelegram.schemas import SourceType, Strategy, TapeCatalog
from totelegram.utils import batched

logger = logging.getLogger(__name__)

MANIFEST_VERSION = "5.0"


class FileFragment(BaseModel):
    """GPS exacto de un archivo dentro de un volumen (Payload)."""

    vol_idx: int  # sequence_index del Payload
    offset_in_vol: int  # Offset de inicio dentro del archivo .tar del volumen
    bytes_in_volume: (
        int  # Cantidad de bytes (o donde termina) este archivo en este volumen
    )


class TapeMemberSnapshot(BaseModel):
    """Representación de un archivo dentro de una carpeta archivada."""

    relative_path: str
    size: int
    md5sum: str
    fragments: List[FileFragment]


class SourceMetadata(BaseModel):
    filename: str
    size: int
    md5sum: str
    mime_type: str
    mtime: float
    type: SourceType
    tape_catalog: Optional[TapeCatalog] = None
    inventory: Optional[List[TapeMemberSnapshot]] = None


class RemotePart(BaseModel):
    sequence: int
    message_id: int
    chat_id: int
    link: str
    part_filename: str
    part_size: int
    part_md5sum: str
    start_offset: int  # Offset global en el Source (virtualización)
    end_offset: int  # Offset global en el Source (virtualización)


class UploadManifest(BaseModel):
    manifest_version: str = MANIFEST_VERSION
    app_version: str = __version__
    created_at: datetime
    strategy: Strategy
    chunk_size: int
    chat_id: int
    owner_id: int
    owner_name: str
    source: SourceMetadata
    parts: List[RemotePart]


def chunk_ranges(file_size: int, chunk_size: int) -> List[Tuple[int, int]]:
    """
    Genera una lista de tuplas (inicio, fin) para la división del archivo.
    El rango 'fin' es exclusivo (no incluido en la lectura).

    Referencia:
        https://chatgpt.com/share/68a6ec82-8874-8012-9c27-af04127e28b0

    Args:
        file_size: Tamaño total del archivo.
        chunk_size: Tamaño del bloque deseado.

    Returns:
        List[Tuple[int, int]]: Lista de coordenadas [(0, 100), (100, 200), ...].
    """
    return [
        (start, min(start + chunk_size, file_size))
        for start in range(0, file_size, chunk_size)
    ]


def build_payload_names(source: Source, idx: int, total: int) -> Tuple[str, str]:
    source_path = Path(source.path_str)

    if source.type == SourceType.FOLDER:
        original_ext = ".tar"
        base_human_name = source_path.name
        combat_hash = source.md5sum[:40]
    else:
        original_ext = source_path.suffix
        base_human_name = source_path.name
        if original_ext:
            base_human_name = base_human_name[: -len(original_ext)]
        combat_hash = source.md5sum

    if total > 1:
        padding = max(2, len(str(total)))
        part_suffix = f".{str(idx + 1).zfill(padding)}-{str(total).zfill(padding)}"
    else:
        part_suffix = ""

    # Human: "Mi Carpeta.tar.01-10" o "Video.mp4.01-10"
    full_name = f"{base_human_name}{original_ext}{part_suffix}"

    # Short: "sha40.tar.01-10" o "md532.mp4.01-10"
    short_name = f"{combat_hash}{original_ext}{part_suffix}"

    return full_name, short_name


class Chunker:
    @classmethod
    def get_or_create(cls, job: Job) -> List[Payload]:
        """Decide la segmentación basándose en el tipo de recurso."""

        if job.payloads.count() > 0:
            logger.debug(f"El Job {job.id} ya tiene payloads. Saltando segmentación.")
            return list(job.payloads.order_by(Payload.sequence_index))

        if job.source.type == SourceType.FOLDER:
            return cls._process_folder_job(job)
        else:
            return cls._process_file_job(job)

    @classmethod
    def _process_file_job(cls, job: Job) -> List[Payload]:
        limit = job.config.tg_max_size
        ranges = chunk_ranges(file_size=job.source.size, chunk_size=limit)

        payloads_data = []
        for idx, (start, end) in enumerate(ranges):
            filename, filename_short = build_payload_names(
                source=job.source,  # FIX: consulta N+1
                idx=idx,
                total=len(ranges),
            )

            payloads_data.append(
                {
                    "job": job,
                    "sequence_index": idx,
                    "start_offset": start,
                    "end_offset": end,
                    "size": end - start,
                    "filename": filename,
                    "filename_short": filename_short,
                    "md5sum": None,  # Se calculará durante la subida real
                }
            )

        for batch in batched(payloads_data, 100):
            Payload.insert_many(batch).execute()

        return list(job.payloads.order_by(Payload.sequence_index))

    @classmethod
    def _process_folder_job(cls, job: Job) -> List[Payload]:
        from tartape.chunker import TarChunker

        # tape = tartape.discover(job.source.path)
        tar_chunker = TarChunker(chunk_size=job.config.tg_max_size)
        vols = list(tar_chunker.iter_volumes(job.source.path))

        count_parts = len(vols)
        payloads = []
        for idx, (vol, manifest) in enumerate(vols):
            filename, filename_short = build_payload_names(
                source=job.source, idx=idx, total=count_parts
            )

            payload = Payload.create(
                job=job,
                sequence_index=manifest.volume_index,
                start_offset=manifest.start_offset,
                end_offset=manifest.end_offset,
                size=manifest.chunk_size,
                filename=filename,
                filename_short=filename_short,
            )

            TapeMember.register_manifest_entries(
                source=job.source,
                payload=payload,
                entries=manifest.entries,
            )

            payloads.append(payload)

        return payloads


class SnapshotService:
    @staticmethod
    def generate_snapshot(job: Job):
        source = job.source
        original_file_path = Path(source.path_str)

        # Obtenemos los registros remotos
        remotes_db = (
            RemotePayload.select(RemotePayload, Payload)
            .join(Payload)
            .where((Payload.job == job) & (RemotePayload.is_orphaned == False))
            .order_by(Payload.sequence_index)
        )

        if not remotes_db.exists():
            raise ValueError(
                f"No hay registros remotos para el Job {job.id}. Imposible crear snapshot."
            )

        # Mapa de Mensajes en Telegram
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

        # Construimos el inventario con el GPS de los archivos
        inventory: Optional[List[TapeMemberSnapshot]] = None
        if source.type == SourceType.FOLDER:
            inventory = []
            # TapeMember -> TapeMemberGPS -> Payload
            members = cast(
                List[TapeMember],
                TapeMember.select()
                .where(TapeMember.source == source)
                .prefetch(TapeMemberGPS, Payload),
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

        # Metadatos del Origen (Archivo o Carpeta)
        owner = remotes_db[0].owner
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

        # Crear Manifiesto Final
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

        # Guardado Atómico Comprimido
        output_path = SnapshotService._resolve_snapshot_path(
            original_file_path, source.md5sum
        )
        with lzma.open(output_path, "wt", encoding="utf-8") as f:
            f.write(manifest.model_dump_json(indent=2))

        return manifest

    @staticmethod
    def _resolve_snapshot_path(file_path: Path, current_md5: str) -> Path:
        """Resuelve el nombre del archivo evitando colisiones (ADR-003)."""
        base_target = file_path.with_name(f"{file_path.name}.json.xz")

        # Si no existe, es la ruta ideal
        if not base_target.exists():
            return base_target

        try:
            with lzma.open(base_target, "rt", encoding="utf-8") as f:
                import json

                existing_data = json.load(f)
                if existing_data.get("source", {}).get("md5sum") == current_md5:
                    return base_target
        except Exception:
            pass

        # Si es un recurso distinto, numerar: archivo (1).ext.json.xz
        counter = 1
        while True:
            new_target = file_path.with_name(
                f"{file_path.stem} ({counter}){file_path.suffix}.json.xz"
            )
            if not new_target.exists():
                return new_target
            counter += 1
