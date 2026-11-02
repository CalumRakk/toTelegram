import enum
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from peewee import prefetch

from totelegram.models import Job, RemoteSnapshot, Source, TelegramChat
from totelegram.schemas import SourceType
from totelegram.utils import has_snapshot

logger = logging.getLogger(__name__)


class AuditStatus(str, enum.Enum):
    """Estados posibles de un archivo o carpeta durante la auditoría."""

    BACKED_UP = "backed_up"  # ✓ Respaldado (En DB y en Telegram)
    LOCAL_SNAPSHOT = "local_snapshot"  # ✓ Respaldado (Snapshot local detectado sin DB)
    MODIFIED = "modified"  # ! Modificado localmente (desincronizado)
    PENDING = "pending"  # Pendiente de subida
    REMOTE_ONLY = "remote_only"  # ☁ Solo en Telegram (borrado de disco / podado)
    TAPE_BACKUP = "tape_backup"  # ✓ Cinta TAR (Carpeta respaldada como cinta)
    DIRECTORY = "directory"  # Directorio normal (Estado neutral)


@dataclass
class AuditItem:
    """Resultado de la auditoría para un archivo o carpeta individual."""

    filename: str
    path: Path | None
    size: int
    is_dir: bool
    status: AuditStatus
    destinations: list[str] = field(default_factory=list)

    @property
    def is_safe_to_prune(self) -> bool:
        """
        Solo es 100% seguro de podar si es un archivo regular en disco,
        está respaldado formalmente en Telegram y no ha sufrido modificaciones locales.
        """
        return (
            not self.is_dir
            and self.path is not None
            and self.path.is_file()
            and self.status == AuditStatus.BACKED_UP
        )


class LocalAuditService:
    """
    Servicio encargado de contrastar el sistema de archivos local contra SQLite.
    Garantiza velocidad O(1) precargando registros y evitando abrir o parsear snapshots.
    """

    @classmethod
    def audit_path(
        cls,
        target_path: Path | str,
        target_chat_id: int | str | None = None,
    ) -> list[AuditItem]:
        """
        Audita una ruta específica (archivo o directorio a primer nivel).

        Args:
            target_path: Archivo o directorio a inspeccionar.
            target_chat_id: Filtrar por un chat de Telegram específico (opcional).

        Returns:
            list[AuditItem]: Lista de elementos auditados con su estado y destinos.
        """
        path = Path(target_path).resolve()
        if not path.exists():
            return []

        if path.is_file():
            return cls._audit_single_file(path, target_chat_id=target_chat_id)
        else:
            return cls._audit_directory_shallow(path, target_chat_id=target_chat_id)

    @classmethod
    def _audit_single_file(
        cls,
        file_path: Path,
        target_chat_id: int | str | None = None,
    ) -> list[AuditItem]:
        """Audita un único archivo puntual."""
        canonical_str = str(file_path.resolve())
        stat = file_path.stat()
        item = cls._resolve_file_status(
            file_path=file_path,
            stat=stat,
            source_match=cls._find_source_for_file(file_path, canonical_str, stat),
            target_chat_id=target_chat_id,
        )
        return [item]

    @classmethod
    def _audit_directory_shallow(
        cls,
        dir_path: Path,
        target_chat_id: int | str | None = None,
    ) -> list[AuditItem]:
        """
        Audita el primer nivel de un directorio (no recursivo).
        Precarga todos los Source bajo este directorio en un solo query.
        """
        canonical_dir_str = str(dir_path.resolve())
        prefix = (
            canonical_dir_str
            if canonical_dir_str.endswith(os.sep)
            else f"{canonical_dir_str}{os.sep}"
        )

        # 1. Precarga en lote de Sources y sus Jobs/Snapshots asociados
        sources_query = Source.select().where(Source.path_str.startswith(prefix))
        jobs_query = Job.select()
        chats_query = TelegramChat.select()
        snapshots_query = RemoteSnapshot.select().where(
            RemoteSnapshot.is_orphaned == False
        )

        prefetched_sources = list(
            prefetch(sources_query, jobs_query, chats_query, snapshots_query)
        )
        sources_by_path: dict[str, Source] = {s.path_str: s for s in prefetched_sources}

        results: list[AuditItem] = []
        seen_paths: set[str] = set()

        # 2. Exploración de primer nivel de elementos físicos en disco
        for child in sorted(dir_path.iterdir()):
            if cls._is_internal_ignored(child):
                continue

            child_canonical = str(child.resolve())
            seen_paths.add(child_canonical)

            if child.is_dir():
                results.append(
                    cls._resolve_directory_status(
                        dir_path=child,
                        canonical_path=child_canonical,
                        sources_by_path=sources_by_path,
                        target_chat_id=target_chat_id,
                    )
                )
            elif child.is_file():
                stat = child.stat()
                source_match = sources_by_path.get(child_canonical)
                if source_match is None:
                    # Camino 2: Buscar si fue movido o renombrado
                    source_match = cls._find_moved_file_in_db(child, stat)

                results.append(
                    cls._resolve_file_status(
                        file_path=child,
                        stat=stat,
                        source_match=source_match,
                        target_chat_id=target_chat_id,
                    )
                )

        # 3. Detectar elementos registrados en DB que ya fueron eliminados del disco (REMOTE_ONLY)
        for s in prefetched_sources:
            if s.path_str not in seen_paths and s.type == SourceType.FILE:
                verified_dests = cls._extract_verified_destinations(s, target_chat_id)
                if verified_dests:
                    results.append(
                        AuditItem(
                            filename=Path(s.path_str).name,
                            path=None,
                            size=s.size,
                            is_dir=False,
                            status=AuditStatus.REMOTE_ONLY,
                            destinations=verified_dests,
                        )
                    )

        return results

    @classmethod
    def _resolve_file_status(
        cls,
        file_path: Path,
        stat: os.stat_result,
        source_match: Source | None,
        target_chat_id: int | str | None,
    ) -> AuditItem:
        """Determina el estado de un archivo evaluando DB y snapshots locales."""
        filename = file_path.name
        size = stat.st_size
        mtime = stat.st_mtime

        # A) Coincidencia en base de datos
        if source_match is not None:
            # Comprobar si el archivo fue modificado localmente
            is_modified = (source_match.size != size) or not Source._is_mtime_matching(
                source_match.mtime, mtime
            )

            verified_dests = cls._extract_verified_destinations(
                source_match, target_chat_id
            )

            if is_modified:
                return AuditItem(
                    filename=filename,
                    path=file_path,
                    size=size,
                    is_dir=False,
                    status=AuditStatus.MODIFIED,
                    destinations=verified_dests,
                )

            if verified_dests:
                return AuditItem(
                    filename=filename,
                    path=file_path,
                    size=size,
                    is_dir=False,
                    status=AuditStatus.BACKED_UP,
                    destinations=verified_dests,
                )

        # B) Comprobación de snapshot local como testigo visual (sin parseo)
        if has_snapshot(file_path):
            return AuditItem(
                filename=filename,
                path=file_path,
                size=size,
                is_dir=False,
                status=AuditStatus.LOCAL_SNAPSHOT,
                destinations=["Local (Snapshot)"],
            )

        # C) Archivo pendiente de subida
        return AuditItem(
            filename=filename,
            path=file_path,
            size=size,
            is_dir=False,
            status=AuditStatus.PENDING,
            destinations=[],
        )

    @classmethod
    def _resolve_directory_status(
        cls,
        dir_path: Path,
        canonical_path: str,
        sources_by_path: dict[str, Source],
        target_chat_id: int | str | None,
    ) -> AuditItem:
        """Determina el estado de una carpeta (Cinta TAR o Directorio neutral)."""
        filename = dir_path.name

        # Verificar si la carpeta está registrada como cinta en DB
        source = sources_by_path.get(canonical_path)
        if source and source.type == SourceType.FOLDER:
            verified_dests = cls._extract_verified_destinations(source, target_chat_id)
            if verified_dests:
                return AuditItem(
                    filename=filename,
                    path=dir_path,
                    size=source.size,
                    is_dir=True,
                    status=AuditStatus.TAPE_BACKUP,
                    destinations=verified_dests,
                )

        # Verificar si existe un contenedor .ttape para esta carpeta
        ttape_candidate = dir_path.with_name(f"{dir_path.name}.ttape")
        if ttape_candidate.is_file():
            return AuditItem(
                filename=filename,
                path=dir_path,
                size=0,
                is_dir=True,
                status=AuditStatus.TAPE_BACKUP,
                destinations=["Local (.ttape)"],
            )

        # Estado Neutral: Directorio regular sin backup registrado
        return AuditItem(
            filename=filename,
            path=dir_path,
            size=0,
            is_dir=True,
            status=AuditStatus.DIRECTORY,
            destinations=[],
        )

    @classmethod
    def _find_source_for_file(
        cls, file_path: Path, canonical_path: str, stat: os.stat_result
    ) -> Source | None:
        """Busca un Source para un archivo individual (Camino 1 directo, luego Camino 2)."""
        source = Source.get_or_none(Source.path_str == canonical_path)
        if source:
            return source
        return cls._find_moved_file_in_db(file_path, stat)

    @classmethod
    def _find_moved_file_in_db(
        cls, file_path: Path, stat: os.stat_result
    ) -> Source | None:
        """
        Camino 2: Busca si el archivo fue movido o renombrado.
        Compara por tamaño, nombre de archivo y mtime. Si coincide, actualiza la ruta.
        """
        candidates = list(
            Source.select().where(
                (Source.size == stat.st_size) & (Source.type == SourceType.FILE)
            )
        )
        for s in candidates:
            if Path(s.path_str).name == file_path.name and Source._is_mtime_matching(
                s.mtime, stat.st_mtime
            ):
                logger.info(
                    f"Archivo movido detectado: '{s.path_str}' -> '{file_path}'. "
                    "Actualizando ruta en base de datos."
                )
                s.path_str = str(file_path.resolve())
                s.save(only=[Source.path_str, Source.updated_at])
                return s
        return None

    @staticmethod
    def _extract_verified_destinations(
        source: Source, target_chat_id: int | str | None
    ) -> list[str]:
        """Obtiene los nombres de los canales donde el recurso está verificado y activo."""
        destinations = []
        for job in source.jobs:
            if not job.is_completed_local or not job.has_remote_snapshot:
                continue

            if target_chat_id is not None and str(job.chat_id) != str(target_chat_id):
                continue

            title = job.chat.title or f"Chat {job.chat_id}"
            parts_str = f"{job.total_expected_parts}p"
            destinations.append(f"{title} [dim]({parts_str})[/]")
        return destinations

    @staticmethod
    def _is_internal_ignored(path: Path) -> bool:
        """Determina si un archivo debe ignorarse durante el listado."""
        name = path.name
        if name.startswith("."):
            return True
        # Ignorar los sidecars de snapshots en el listado de archivos candidatos
        return bool(name.endswith((".json.xz", ".ttape", ".tartape")))
