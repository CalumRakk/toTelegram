import enum
import json
import logging
import lzma
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Generator, List, Optional, Tuple

import peewee
from pydantic import ValidationError
from tartape.schemas import EntryState

from totelegram import __VERSION__
from totelegram.common.files import get_mimetype
from totelegram.common.helpers import batched
from totelegram.database import db_transaction
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TapeMember,
    TapeMemberGPS,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.schemas import UploadManifest
from totelegram.schemas import JobStatus, SourceType, StrategyConfig

logger = logging.getLogger(__name__)


# DTOs, ENUMS Y CONTRATOS DEL PLAN


class InspectionStatus(str, enum.Enum):
    """Estado de clasificación de un archivo de snapshot tras su lectura física."""

    CURRENT_VERSION = "current_version"  # Coincide con UploadManifest actual
    UNKNOWN_VERSION = "unknown_version"  # JSON válido pero esquema no reconocido
    CORRUPTED = "corrupted"  # Archivo dañado, error de descompresión o JSON inválido


class PlanAction(str, enum.Enum):
    """Acción que el planificador propone ejecutar sobre la base de datos."""

    CREATE_NEW = "create_new"  # Source y Job no existen, inserción limpia
    CREATE_JOB = "create_job"  # Source existe, crear Job para un nuevo chat
    MERGE_REMOTES = "merge_remotes"  # Job existe pero faltan partes remotas
    ALREADY_EXISTS = "already_exists"  # Ya registrado y completo al 100% (No-Op)
    CONFLICT = "conflict"  # Discrepancia grave en la estructura (Omitir)


@dataclass
class SnapshotInspectionResult:
    """Resultado del diagnóstico sintáctico de un archivo de snapshot."""

    path: Path
    status: InspectionStatus
    manifest: Optional[UploadManifest] = None
    root_keys: List[str] = field(default_factory=list)
    error_message: Optional[str] = None


@dataclass
class PlanItem:
    """Representa una entrada individual dentro del plan de importación."""

    snapshot_path: Path
    action: PlanAction
    manifest: Optional[UploadManifest] = None
    md5sum: str = ""
    chat_id: Optional[int] = None
    total_size: int = 0
    parts_count: int = 0
    message: str = ""


@dataclass
class ImportPlan:
    """Plan global consolidado que agrupa todas las acciones a ejecutar."""

    items: List[PlanItem] = field(default_factory=list)

    @property
    def total_items(self) -> int:
        return len(self.items)

    @property
    def ready_to_import(self) -> List[PlanItem]:
        return [
            item
            for item in self.items
            if item.action
            in (PlanAction.CREATE_NEW, PlanAction.CREATE_JOB, PlanAction.MERGE_REMOTES)
        ]

    @property
    def already_present(self) -> List[PlanItem]:
        return [item for item in self.items if item.action == PlanAction.ALREADY_EXISTS]

    @property
    def conflicts(self) -> List[PlanItem]:
        return [item for item in self.items if item.action == PlanAction.CONFLICT]


@dataclass
class ImportExecutionReport:
    """Reporte consolidado tras aplicar el plan en la base de datos."""

    total_attempted: int = 0
    successful: int = 0
    skipped: int = 0
    failed: int = 0
    errors: List[Tuple[Path, str]] = field(default_factory=list)


class SnapshotInspector:
    """
    Inspecciona y clasifica archivos de snapshot (.json.xz).
    Aísla las versiones desconocidas y corruptas para proteger el flujo principal.
    """

    @classmethod
    def inspect_file(cls, path: Path) -> SnapshotInspectionResult:
        """Lee y valida un único archivo snapshot."""
        if not path.is_file():
            return SnapshotInspectionResult(
                path=path,
                status=InspectionStatus.CORRUPTED,
                error_message="La ruta no es un archivo válido.",
            )

        try:
            with lzma.open(path, "rt", encoding="utf-8") as f:
                raw_data = json.load(f)
        except lzma.LZMAError as e:
            return SnapshotInspectionResult(
                path=path,
                status=InspectionStatus.CORRUPTED,
                error_message=f"Fallo de descompresión LZMA: {e}",
            )
        except json.JSONDecodeError as e:
            return SnapshotInspectionResult(
                path=path,
                status=InspectionStatus.CORRUPTED,
                error_message=f"JSON malformado: {e}",
            )
        except Exception as e:
            return SnapshotInspectionResult(
                path=path,
                status=InspectionStatus.CORRUPTED,
                error_message=f"Error de lectura I/O: {e}",
            )

        if not isinstance(raw_data, dict):
            return SnapshotInspectionResult(
                path=path,
                status=InspectionStatus.CORRUPTED,
                error_message="El contenido raíz del JSON no es un objeto/diccionario.",
            )

        root_keys = sorted(list(raw_data.keys()))

        # Intentar validar contra el esquema canónico actual
        try:
            manifest = UploadManifest.model_validate(raw_data)
            return SnapshotInspectionResult(
                path=path,
                status=InspectionStatus.CURRENT_VERSION,
                manifest=manifest,
                root_keys=root_keys,
            )
        except (ValidationError, KeyError, TypeError) as e:
            logger.debug(f"Snapshot {path.name} no coincide con el esquema actual: {e}")
            return SnapshotInspectionResult(
                path=path,
                status=InspectionStatus.UNKNOWN_VERSION,
                root_keys=root_keys,
                error_message=str(e),
            )

    @classmethod
    def scan_paths(cls, paths: List[Path]) -> List[SnapshotInspectionResult]:
        """
        Escanea de forma recursiva una lista de rutas (archivos directos o directorios)
        y clasifica todos los archivos con extensión .json.xz.
        """
        results: List[SnapshotInspectionResult] = []
        seen_paths = set()

        for target in paths:
            if not target.exists():
                continue

            candidates: Generator[Path, None, None]
            if target.is_file():
                candidates = (target for _ in range(1))
            else:
                candidates = target.rglob("*.json.xz")

            for candidate in candidates:
                resolved = candidate.resolve()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)

                if candidate.name.endswith(".json.xz"):
                    results.append(cls.inspect_file(candidate))

        return results


class SnapshotImportPlanner:
    """
    Analiza los snapshots válidos contra el estado de la base de datos.
    Genera un ImportPlan determinista sin realizar ninguna escritura.
    """

    @classmethod
    def build_plan(
        cls,
        inspections: List[SnapshotInspectionResult],
        db: peewee.Database,
    ) -> ImportPlan:
        plan = ImportPlan()

        for insp in inspections:
            if insp.status != InspectionStatus.CURRENT_VERSION or not insp.manifest:
                continue

            manifest = insp.manifest
            source_md5 = manifest.source.md5sum
            parts = manifest.parts

            if not parts:
                plan.items.append(
                    PlanItem(
                        snapshot_path=insp.path,
                        action=PlanAction.CONFLICT,
                        manifest=manifest,
                        md5sum=source_md5,
                        total_size=manifest.source.size,
                        message="El snapshot no contiene piezas/partes registradas.",
                    )
                )
                continue

            chat_id = parts[0].chat_id
            total_size = manifest.source.size
            parts_count = len(parts)

            # 1. Comprobar si el Source existe por su MD5 / Fingerprint único
            existing_source = Source.get_or_none(Source.md5sum == source_md5)

            if existing_source is None:
                plan.items.append(
                    PlanItem(
                        snapshot_path=insp.path,
                        action=PlanAction.CREATE_NEW,
                        manifest=manifest,
                        md5sum=source_md5,
                        chat_id=chat_id,
                        total_size=total_size,
                        parts_count=parts_count,
                        message="Contenido nuevo. Se creará Source, Job y piezas.",
                    )
                )
                continue

            # 2. El Source ya existe: verificar si existe un Job activo para este Chat
            existing_job = Job.get_or_none(
                (Job.source == existing_source)
                & (Job.chat == chat_id)
                & (Job.deleted_at == 0)
            )

            if existing_job is None:
                plan.items.append(
                    PlanItem(
                        snapshot_path=insp.path,
                        action=PlanAction.CREATE_JOB,
                        manifest=manifest,
                        md5sum=source_md5,
                        chat_id=chat_id,
                        total_size=total_size,
                        parts_count=parts_count,
                        message="Source existente. Se vinculará un nuevo Job a este Chat.",
                    )
                )
                continue

            # 3. El Source y el Job ya existen en este Chat: evaluar integridad de partes
            active_remotes_count = (
                RemotePayload.select()
                .join(Payload)
                .where(
                    (Payload.job == existing_job) & (RemotePayload.is_orphaned == False)  # noqa: E712
                )
                .count()
            )

            if active_remotes_count == parts_count:
                plan.items.append(
                    PlanItem(
                        snapshot_path=insp.path,
                        action=PlanAction.ALREADY_EXISTS,
                        manifest=manifest,
                        md5sum=source_md5,
                        chat_id=chat_id,
                        total_size=total_size,
                        parts_count=parts_count,
                        message="Ya registrado e íntegro en la base de datos.",
                    )
                )
            else:
                plan.items.append(
                    PlanItem(
                        snapshot_path=insp.path,
                        action=PlanAction.MERGE_REMOTES,
                        manifest=manifest,
                        md5sum=source_md5,
                        chat_id=chat_id,
                        total_size=total_size,
                        parts_count=parts_count,
                        message=f"Faltan piezas en DB ({active_remotes_count}/{parts_count}). Se completarán.",
                    )
                )

        return plan


class SnapshotImporter:
    """
    Ejecutor transaccional del plan de importación.
    Aplica las acciones planificadas garantizando aislamiento atómico por cada snapshot.
    """

    def __init__(self, db: peewee.Database, default_owner: TelegramUser):
        self.db = db
        self.default_owner = default_owner

    def apply(
        self,
        plan: ImportPlan,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> ImportExecutionReport:
        """
        Ejecuta las acciones de inserción y vinculación en la base de datos.
        Cada snapshot se ejecuta en su propia transacción para asegurar resiliencia en lote.
        """
        report = ImportExecutionReport()
        actionable_items = plan.ready_to_import

        report.total_attempted = len(actionable_items)
        report.skipped = len(plan.items) - len(actionable_items)

        for idx, item in enumerate(actionable_items, 1):
            manifest = item.manifest
            if not manifest:
                continue

            if on_progress:
                on_progress(idx, len(actionable_items), item.snapshot_path.name)

            try:
                with db_transaction(self.db):
                    if item.action == PlanAction.CREATE_NEW:
                        self._execute_create_new(manifest)
                    elif item.action == PlanAction.CREATE_JOB:
                        self._execute_create_job(manifest)
                    elif item.action == PlanAction.MERGE_REMOTES:
                        self._execute_merge_remotes(manifest)

                report.successful += 1
                logger.debug(
                    f"Importado con éxito: {item.snapshot_path.name} [{item.action.value}]"
                )

            except Exception as e:
                report.failed += 1
                report.errors.append((item.snapshot_path, str(e)))
                logger.error(
                    f"Error importando snapshot {item.snapshot_path.name}: {e}",
                    exc_info=True,
                )

        return report

    def _resolve_chat(self, chat_id: int) -> TelegramChat:
        """Obtiene o crea un registro de chat provisional si no existía previamente."""
        chat, _ = TelegramChat.get_or_create(
            id=chat_id,
            defaults={
                "title": f"Chat {chat_id}",
                "type": "unknown",
                "is_public": False,
                "last_verified": datetime.now(timezone.utc),
            },
        )
        return chat

    def _execute_create_new(self, manifest: UploadManifest) -> Job:
        """Inserción completa de un nuevo Source, Job, Payloads y Remotes."""
        first_part = manifest.parts[0]
        chat = self._resolve_chat(first_part.chat_id)

        source_type = manifest.source.type
        mimetype = (
            "application/x-tar"
            if source_type == SourceType.FOLDER
            else get_mimetype(Path(manifest.source.filename))
        )

        source = Source.create(
            md5sum=manifest.source.md5sum,
            path_str=manifest.source.filename,
            size=manifest.source.size,
            mtime=manifest.source.mtime or datetime.now(timezone.utc).timestamp(),
            mimetype=mimetype,
            type=source_type,
            tape_catalog=getattr(manifest.source, "tape_catalog", None),
        )

        max_part_size = max(p.part_size for p in manifest.parts)
        config = StrategyConfig(
            tg_max_size=max_part_size,
            user_is_premium=False,
            app_version=__VERSION__,
        )

        job = Job.create(
            source=source,
            chat=chat,
            strategy=manifest.strategy,
            status=JobStatus.UPLOADED,
            config=config,
            deleted_at=0,
        )

        self._insert_payloads_and_remotes(job, manifest, chat)

        if source_type == SourceType.FOLDER and manifest.source.inventory:
            self._insert_folder_inventory(source, job, manifest)

        return job

    def _execute_create_job(self, manifest: UploadManifest) -> Job:
        """Crea un nuevo Job para un Source existente (ej: subido a otro chat)."""
        first_part = manifest.parts[0]
        chat = self._resolve_chat(first_part.chat_id)
        source = Source.get(Source.md5sum == manifest.source.md5sum)

        max_part_size = max(p.part_size for p in manifest.parts)
        config = StrategyConfig(
            tg_max_size=max_part_size,
            user_is_premium=False,
            app_version=__VERSION__,
        )

        job = Job.create(
            source=source,
            chat=chat,
            strategy=manifest.strategy,
            status=JobStatus.UPLOADED,
            config=config,
            deleted_at=0,
        )

        self._insert_payloads_and_remotes(job, manifest, chat)
        return job

    def _execute_merge_remotes(self, manifest: UploadManifest) -> None:
        """Completa los RemotePayloads faltantes en un Job existente."""
        first_part = manifest.parts[0]
        chat = self._resolve_chat(first_part.chat_id)
        source = Source.get(Source.md5sum == manifest.source.md5sum)
        job = Job.get_for_source_in_chat(source, chat)

        if not job:
            return

        existing_payloads = {p.sequence_index: p for p in job.payloads}  # type: ignore

        for part in manifest.parts:
            payload = existing_payloads.get(part.sequence)
            if not payload:
                continue

            remote, created = RemotePayload.get_or_create(
                payload=payload,
                chat=chat,
                defaults={
                    "message_id": part.message_id,
                    "owner": self.default_owner,
                    "json_metadata": {
                        "id": part.message_id,
                        "chat": {"id": chat.id},
                    },
                    "last_verified_at": datetime.now(timezone.utc),
                    "is_orphaned": False,
                },
            )
            if not created and (
                remote.is_orphaned or remote.message_id != part.message_id
            ):
                remote.message_id = part.message_id
                remote.is_orphaned = False
                remote.last_verified_at = datetime.now(timezone.utc)
                remote.save(
                    only=[
                        RemotePayload.message_id,
                        RemotePayload.is_orphaned,
                        RemotePayload.last_verified_at,
                        RemotePayload.updated_at,
                    ]
                )

        job.status = JobStatus.UPLOADED
        job.save(only=[Job.status, Job.updated_at])

    def _insert_payloads_and_remotes(
        self, job: Job, manifest: UploadManifest, chat: TelegramChat
    ) -> None:
        """Inserta los Payloads y RemotePayloads en bloque optimizado."""
        payloads_data = [
            {
                "job": job,
                "sequence_index": part.sequence,
                "start_offset": getattr(part, "start_offset", 0),
                "end_offset": getattr(part, "end_offset", part.part_size),
                "size": part.part_size,
                "filename": part.part_filename,
                "filename_short": getattr(
                    part, "part_filename_short", part.part_filename
                ),
                "md5sum": part.part_md5sum,
            }
            for part in manifest.parts
        ]
        Payload.insert_many(payloads_data).execute()

        db_payloads = {
            p.sequence_index: p for p in Payload.select().where(Payload.job == job)
        }

        remotes_data = [
            {
                "payload": db_payloads[part.sequence],
                "message_id": part.message_id,
                "chat": chat,
                "owner": self.default_owner,
                "json_metadata": {
                    "id": part.message_id,
                    "chat": {"id": chat.id},
                },
                "last_verified_at": datetime.now(timezone.utc),
                "is_orphaned": False,
            }
            for part in manifest.parts
            if part.sequence in db_payloads
        ]
        RemotePayload.insert_many(remotes_data).execute()

    def _insert_folder_inventory(
        self, source: Source, job: Job, manifest: UploadManifest
    ) -> None:
        """Reconstruye TapeMember y TapeMemberGPS si el snapshot incluye catálogo de carpeta."""
        inventory = manifest.source.inventory or []
        if not inventory:
            return

        member_inserts = [
            {
                "source": source,
                "relative_path": item.relative_path,
                "size": item.size,
                "md5sum": item.md5sum,
            }
            for item in inventory
        ]
        for batch in batched(member_inserts, 100):
            TapeMember.insert_many(batch).on_conflict_ignore().execute()

        paths = [item.relative_path for item in inventory]
        path_to_id = {
            m.relative_path: m.id
            for m in TapeMember.select(TapeMember.id, TapeMember.relative_path).where(
                (TapeMember.source == source) & (TapeMember.relative_path << paths)  # type: ignore
            )
        }

        payload_map = {
            p.sequence_index: p for p in Payload.select().where(Payload.job == job)
        }

        gps_inserts = []
        for item in inventory:
            member_id = path_to_id.get(item.relative_path)
            fragments = getattr(item, "fragments", [])
            if not member_id or not fragments:
                continue

            for frag in fragments:
                vol_idx = getattr(frag, "vol_idx", 0)
                payload_obj = payload_map.get(vol_idx)
                if not payload_obj:
                    continue

                gps_inserts.append(
                    {
                        "member": member_id,
                        "payload": payload_obj,
                        "state": getattr(
                            frag, "state", EntryState.COMPLETED
                        ),  # TODO : VERIFICAR QUE SEA REALMENTE .COMPLETED
                        "offset_in_volume": getattr(frag, "offset_in_vol", 0),
                        "bytes_in_volume": getattr(frag, "bytes_in_volume", item.size),
                    }
                )

        if gps_inserts:
            for batch in batched(gps_inserts, 200):
                TapeMemberGPS.insert_many(batch).execute()
