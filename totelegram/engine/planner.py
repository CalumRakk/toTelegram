import logging
from pathlib import Path

import peewee
import tartape
from tartape.exceptions import PathConstraintReportError, TarIntegrityError

from totelegram.engine.events import NullObserver, UploadObserver
from totelegram.exceptions import PathLengthExceededError
from totelegram.identity import Settings
from totelegram.models import Job, Source, TelegramChat, TelegramUser
from totelegram.types import UploadSession
from totelegram.utils import delete_snapshot

logger = logging.getLogger(__name__)


class JobPlanner:
    @classmethod
    def plan(
        cls,
        path: Path,
        session: UploadSession | None = None,
        observer: UploadObserver | None = None,
        force: bool = False,
        auto_truncate: bool = False,
        *,
        tg_chat: object | None = None,
        settings: Settings | None = None,
        owner: TelegramUser | None = None,
    ) -> Job:
        """
        Planifica el Job de forma offline (disco y base de datos local).
        Puede recibir un UploadSession completo o los objetos desacoplados (tg_chat, settings, owner).
        """
        chat_obj = tg_chat or (session.tg_chat if session else None)
        actual_settings = settings or (session.settings if session else None)
        actual_owner = owner or (session.owner if session else None)

        if chat_obj is None or actual_settings is None or actual_owner is None:
            raise ValueError(
                "Se requieren tg_chat, settings y owner para planificar el Job."
            )

        # Si forzamos, limpiamos snapshots o catálogos residuales ANTES de generar los nuevos
        if force:
            delete_snapshot(path)

        if isinstance(chat_obj, TelegramChat):
            chat_db = chat_obj
        else:
            chat_db, _ = TelegramChat.get_or_create_from_chat(chat_obj)  # type: ignore

        obs = observer or NullObserver()

        # Genera el Source y su huella/catálogo TAR 100% en disco local (sin red)
        if path.is_dir():
            source = cls._resolve_folder_source(
                path, actual_settings, force, auto_truncate
            )
        else:
            source = cls._resolve_file_source(path, obs)

        # Buscamos si existe un Job previo en este chat (incompleto o completado)
        existing_job = Job.get_for_source_in_chat(source, chat_db)

        if existing_job:
            job = existing_job
        else:
            chunk_size = (
                actual_settings.tg_max_size_premium
                if actual_owner.is_premium
                else actual_settings.tg_max_size_normal
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
        settings: Settings,
        force: bool,
        auto_truncate: bool,
    ) -> Source:
        """Obtiene o indexa la cinta TAR de una carpeta usando tartape de forma offline."""
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

        exclusion_patterns = settings.exclude_files
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
