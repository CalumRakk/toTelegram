import logging
from pathlib import Path

import peewee
import tartape
from tartape.exceptions import PathConstraintReportError, TarIntegrityError

from totelegram.engine.events import NullObserver, UploadObserver
from totelegram.models import Job, Source, TelegramChat
from totelegram.types import UploadSession
from totelegram.utils import delete_snapshot

logger = logging.getLogger(__name__)


class JobPlanningError(Exception):
    """Excepción base para errores de planificación de Jobs."""


class PathLengthExceededError(JobPlanningError):
    """Lanzada cuando una carpeta contiene rutas que exceden el estándar TAR."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(f"Rutas demasiado largas encontradas en '{path.name}'.")


class JobPlanner:
    @classmethod
    def plan(
        cls,
        path: Path,
        session: UploadSession,
        observer: UploadObserver | None = None,
        force: bool = False,
        auto_truncate: bool = False,
    ) -> Job:
        chat_db, _ = TelegramChat.get_or_create_from_chat(session.tg_chat)
        obs = observer or NullObserver()

        if path.is_dir():
            source = cls._resolve_folder_source(path, session, force, auto_truncate)
        else:
            source = cls._resolve_file_source(path, obs)

        # Buscamos si existe un Job previo en este chat (incompleto o completado)
        existing_job = Job.get_for_source_in_chat(source, chat_db)

        if force:
            delete_snapshot(path)

        if existing_job:
            job = existing_job
        else:
            chunk_size = (
                session.settings.tg_max_size_premium
                if session.owner.is_premium
                else session.settings.tg_max_size_normal
            )
            job = Job.formalize_intent(
                source=source,
                chat=chat_db,
                chunk_size=chunk_size,
            )

        return job

    @classmethod
    def _resolve_file_source(cls, path: Path, observer: UploadObserver) -> Source:
        """Obtiene o registra el Source para un archivo individual comunicando progreso."""
        return Source.get_or_create_from_filepath(
            path,
            on_hash_start=observer.on_hash_start,
            on_hash_progress=observer.on_hash_progress,
            on_hash_complete=observer.on_hash_complete,
        )

    @classmethod
    def _resolve_folder_source(
        cls,
        path: Path,
        session: UploadSession,
        force: bool,
        auto_truncate: bool,
    ) -> Source:
        """Obtiene o indexa la cinta TAR de una carpeta usando tartape."""
        if tartape.exists(path) and not force:
            try:
                tape = tartape.Tape(path)
                tape.verify(deep=False, raise_exception=True)
                source = Source.get_or_create_from_tape(tape)
                source.index_tape_members(tape)
                return source
            except (peewee.DoesNotExist, TarIntegrityError):
                logger.debug(
                    f"Cinta en {path.name} inexistente en DB o corrupta. Re-indexando..."
                )

        exclusion_patterns = session.settings.exclude_files
        try:
            tape = tartape.create(
                path,
                exclude=exclusion_patterns,
                checksum="md5",
                overwrite=force,
                auto_truncate=auto_truncate,
            )
            source = Source.create_from_tape(tape, exclusion_patterns)
            source.index_tape_members(tape)
            return source
        except PathConstraintReportError as e:
            raise PathLengthExceededError(path) from e
