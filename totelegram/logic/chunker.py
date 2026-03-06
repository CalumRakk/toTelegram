import logging
from typing import List, Tuple

import peewee
import tartape

from totelegram.common.enums import SourceType
from totelegram.common.utils import batched
from totelegram.manager.models import Job, Payload, TapeMember

logger = logging.getLogger(__name__)


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


class Chunker:
    @classmethod
    def get_or_create(cls, db: peewee.SqliteDatabase, job: Job) -> List[Payload]:
        """Decide la segmentación basándose en el tipo de recurso."""

        if job.payloads.count() > 0:
            logger.debug(f"El Job {job.id} ya tiene payloads. Saltando segmentación.")
            return list(job.payloads.order_by(Payload.sequence_index))

        with db.atomic():
            if job.source.type == SourceType.FOLDER:
                return cls._process_folder_job(job)
            else:
                return cls._process_file_job(job)

    @classmethod
    def _process_file_job(cls, job: Job) -> List[Payload]:
        limit = job.config.tg_max_size
        ranges = chunk_ranges(file_size=job.source.size, chunk_size=limit)

        # FIX: consulta N+1
        base_filename = job.path.name
        count_parts = len(ranges)
        payloads_data = []
        for idx, (start, end) in enumerate(ranges):
            part_num = idx + 1
            filename = base_filename
            if count_parts > 1:
                filename = f"{base_filename}_{part_num}-{count_parts}"

            payloads_data.append(
                {
                    "job": job,
                    "sequence_index": idx,
                    "start_offset": start,
                    "end_offset": end,
                    "size": end - start,
                    "filename": filename,
                    "md5sum": None,  # Se calculará durante la subida real
                }
            )

        for batch in batched(payloads_data, 100):
            Payload.insert_many(batch).execute()

        return list(job.payloads.order_by(Payload.sequence_index))

    @classmethod
    def _process_folder_job(cls, job: Job) -> List[Payload]:
        from tartape.chunker import TarChunker

        tape = tartape.open(job.source.path)
        tape._open_catalog()
        tar_chunker = TarChunker(
            tape=tape._catalog, chunk_size=job.config.tg_max_size  # type: ignore
        )
        plan = tar_chunker.generate_plan()

        base_filename = job.path.name
        count_parts = len(plan)
        payloads = []
        for idx, manifest in enumerate(plan):
            part_num = idx + 1
            filename = base_filename
            if count_parts > 1:
                filename = f"{base_filename}_{part_num}-{count_parts}"

            payload = Payload.create(
                job=job,
                sequence_index=manifest.volume_index,
                start_offset=manifest.start_offset,
                end_offset=manifest.end_offset,
                size=manifest.chunk_size,
                filename=filename,
            )

            TapeMember.register_manifest_entries(
                source=job.source,
                payload=payload,
                entries=manifest.entries,
            )

            payloads.append(payload)

        return payloads
