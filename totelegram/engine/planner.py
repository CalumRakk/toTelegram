import logging
from pathlib import Path

import peewee
import tartape
from tartape.exceptions import PathConstraintReportError, TarIntegrityError

from totelegram.database import db_transaction
from totelegram.models import Job, Source, TelegramChat
from totelegram.types import UploadContext
from totelegram.utils import delete_snapshot

logger = logging.getLogger(__name__)


class JobPlanningError(Exception):
    """Excepción base para errores de planificación de Jobs."""

    pass


class PathLengthExceededError(JobPlanningError):
    """Lanzada cuando una carpeta contiene rutas que exceden el estándar TAR."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(f"Rutas demasiado largas encontradas en '{path.name}'.")


class JobPlanner:
    """
    Planificador puro e idempotente.
    Convierte una ruta física en un Job con sus Payloads listos en la base de datos.
    """

    @classmethod
    def plan(
        cls,
        path: Path,
        ctx: UploadContext,
        force: bool = False,
        auto_truncate: bool = False,
    ) -> Job:
        """
        Punto de entrada: Obtiene o crea el Job y garantiza que sus Chunks estén preparados.
        """
        chat_db, _ = TelegramChat.get_or_create_from_chat(ctx.tg_chat)

        # Resolver o crear el Source (Archivo simple o Carpeta TAR)
        if path.is_dir():
            source = cls._resolve_folder_source(path, ctx, force, auto_truncate)
        else:
            source = cls._resolve_file_source(path)

        # Buscar si ya existía un Job activo para este source en el chat
        existing_job = Job.get_for_source_in_chat(source, chat_db)

        if existing_job and force:
            logger.info(
                f"Forzando reinicio del Job previo {existing_job.id} para {path.name}"
            )
            delete_snapshot(path)
            with db_transaction(ctx.db):
                existing_job.mark_deleted()
                existing_job = None

        if existing_job:
            job = existing_job
        else:
            # Formalizar nuevo Job según límites de cuenta
            tg_limit = (
                ctx.settings.tg_max_size_premium
                if ctx.owner.is_premium
                else ctx.settings.tg_max_size_normal
            )
            job = Job.formalize_intent(
                source=source,
                chat=chat_db,
                is_premium=ctx.owner.is_premium,
                tg_limit=tg_limit,
            )

        # Asegurar que los payloads (chunks) estén calculados y guardados en DB
        with db_transaction(ctx.db):
            job.prepare_chunks(path, ctx.settings)

        return job

    @classmethod
    def _resolve_file_source(cls, path: Path) -> Source:
        """Obtiene o registra el Source para un archivo individual."""
        return Source.get_or_create_from_filepath(path)

    @classmethod
    def _resolve_folder_source(
        cls,
        path: Path,
        ctx: UploadContext,
        force: bool,
        auto_truncate: bool,
    ) -> Source:
        """Obtiene o indexa la cinta TAR de una carpeta usando tartape."""
        if tartape.exists(path) and not force:
            try:
                tape = tartape.Tape(path)
                tape.verify(raise_exception=True)
                return Source.get_or_create_from_tape(tape)
            except (peewee.DoesNotExist, TarIntegrityError):
                logger.debug(
                    f"Cinta en {path.name} inexistente en DB o corrupta. Re-indexando..."
                )

        exclusion_patterns = ctx.settings.exclude_files
        try:
            tape = tartape.create(
                path,
                exclude=exclusion_patterns,
                calculate_hashes=True,
                overwrite=force,
                auto_truncate=auto_truncate,
            )
            return Source.create_from_tape(tape, exclusion_patterns)
        except PathConstraintReportError as e:
            raise PathLengthExceededError(path) from e
