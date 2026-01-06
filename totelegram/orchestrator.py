import logging
from pathlib import Path

from build.lib.totelegram.utils import is_excluded
from totelegram.core.enums import JobStatus
from totelegram.core.setting import Settings
from totelegram.services.chunking import ChunkingService
from totelegram.services.snapshot import SnapshotService
from totelegram.services.uploader import UploadService
from totelegram.store.database import init_database
from totelegram.store.models import Job, SourceFile
from totelegram.telegram import telegram_client_context

logger = logging.getLogger(__name__)


def upload(target: Path, settings: Settings):
    init_database(settings)
    paths = list(target.glob("*")) if target.is_dir() else [target]
    chunker = ChunkingService(settings)

    if not paths:
        logger.info("No hay archivos para procesar.")
        return []

    snapshots = []
    with telegram_client_context(settings) as client:
        for path in paths:
            if is_excluded(path, settings):
                continue
            uploader = UploadService(client, settings)
            try:
                source = SourceFile.get_or_create_from_path(path)
                job = Job.get_or_create_from_source(source, settings)
                if job.status == JobStatus.UPLOADED:
                    logger.info(f"Job {job.id} ya completado. Verificando snapshot...")
                    SnapshotService.generate_snapshot(job)
                    continue

                payloads = chunker.process_job(job)
                for payload in payloads:
                    try:
                        uploader.upload_payload(payload)
                    except Exception as e:
                        logger.error(f"Fallo subida payload {payload.id}: {e}")
                        all_uploaded = False
                        break

                job.set_uploaded()
                logger.info(f"Job {job.id} finalizado con éxito.")
                yield SnapshotService.generate_snapshot(job)

            except Exception as e:
                logger.error(f"Error procesando ruta {path}: {e}")
                continue

    return snapshots
