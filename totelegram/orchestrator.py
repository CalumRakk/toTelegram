import logging
from pathlib import Path

from build.lib.totelegram.utils import is_excluded
from totelegram.enums import JobStatus
from totelegram.models import Job, SourceFile, init_database
from totelegram.services.chunking import ChunkingService
from totelegram.services.snapshot import SnapshotService
from totelegram.services.uploader import UploadService
from totelegram.setting import Settings
from totelegram.telegram import telegram_client_context

logger = logging.getLogger(__name__)


def is_excluded(path: Path, settings: "Settings") -> bool:
    """Devuelve True si el path debe ser excluido según las reglas de exclusión."""
    logger.info(f"Comprobando path exclusion de {path=}")
    if not path.exists():
        logger.info(f"No existe: {path}, se omite")
        return True
    elif path.is_dir():
        logger.info(f"Es un directorio: {path}, se omite")
        return True
    elif settings.is_excluded(path):
        logger.info(f"Está excluido por configuración: {path}, se omite ")
        return True
    elif settings.is_excluded_default(path):
        logger.info(f"Está excluido por configuración: {path}, se omite ")
        return True
    return False


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
                source_asset = SourceFile.get_or_create_from_path(path)
                job = Job.get_or_create_from_source(source_asset, settings)
                if job.status == JobStatus.UPLOADED:
                    logger.info(f"Job {job.id} ya completado. Verificando snapshot...")
                    SnapshotService.generate_snapshot(job)
                    continue

                payloads = chunker.process_job(job)

                all_uploaded = True
                for payload in payloads:
                    try:
                        uploader.upload_payload(payload)
                    except Exception as e:
                        logger.error(f"Fallo subida payload {payload.id}: {e}")
                        all_uploaded = False
                        break

                if all_uploaded:
                    job.status = JobStatus.UPLOADED
                    job.save()
                    snapshot = SnapshotService.generate_snapshot(job)
                    snapshots.append(snapshot)
                    logger.info(f"Job {job.id} finalizado con éxito.")

            except Exception as e:
                logger.error(f"Error procesando ruta {path}: {e}")
                continue

    return snapshots
