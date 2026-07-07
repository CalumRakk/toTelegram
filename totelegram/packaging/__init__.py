from pathlib import Path
from typing import List, cast

from totelegram.models import Job, Payload
from totelegram.packaging.adapter import PackagingPersistenceAdapter
from totelegram.packaging.partitioner import StatelessPartitioner
from totelegram.schemas import SourceType


def prepare_chunks(job: Job, path: Path, settings) -> List[Payload]:
    if job.payloads.count() > 0:
        return cast(List[Payload], list(job.payloads.order_by(Payload.sequence_index)))

    if job.source.type == SourceType.FOLDER:
        # Ejecutar motor físico
        catalog, chunks, members = StatelessPartitioner.partition_folder(
            path, job.config.tg_max_size, settings.exclude_files
        )
        # Guardar a través del adaptador de base de datos
        return PackagingPersistenceAdapter.persist_folder_chunks(job, chunks, members)
    else:
        # Ejecutar motor físico
        chunks = StatelessPartitioner.partition_file(
            path, job.config.tg_max_size, job.source.md5sum
        )
        # Guardar a través del adaptador de base de datos
        return PackagingPersistenceAdapter.persist_chunks(job, chunks)
