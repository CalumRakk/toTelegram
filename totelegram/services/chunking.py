import logging
from typing import List

from totelegram.enums import Strategy
from totelegram.filechunker import FileChunker
from totelegram.models import Job, Payload
from totelegram.setting import Settings

logger = logging.getLogger(__name__)


class ChunkingService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def process_job(self, job: Job) -> List[Payload]:
        """
        Prepara los Payloads para un Job.
        - Si es SINGLE: Crea un Payload único apuntando al source.
        - Si es CHUNKED: Divide el archivo (o recupera trozos) y crea N Payloads.
        """

        if job.payloads.count() > 0:  # type: ignore
            logger.debug(f"Job {job.id} ya tiene payloads generados. Recuperando...")
            return list(job.payloads.order_by(Payload.sequence_index))  # type: ignore

        if job.strategy == Strategy.SINGLE:

            logger.info(f"Job {job.id}: Estrategia SINGLE. Creando payload único.")
            return Payload.create_payloads(job, [job.path])

        elif job.strategy == Strategy.CHUNKED:

            logger.info(f"Job {job.id}: Estrategia CHUNKED. Iniciando división...")

            chunks_folder = self.settings.worktable / "chunks"
            chunks_paths = FileChunker.split_file(
                file_path=job.path,
                chunk_size=self.settings.max_filesize_bytes,
                output_folder=chunks_folder,
            )

            payloads = Payload.create_payloads(job, chunks_paths)
            job.set_splitted()
            return payloads

        return []
