import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import peewee

from totelegram import __VERSION__
from totelegram.database import db_transaction
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.schemas import MANIFEST_VERSION, UploadManifest
from totelegram.packaging.upgrader import SnapshotUpgrader, load_raw_snapshot
from totelegram.schemas import JobStatus, StrategyConfig

logger = logging.getLogger(__name__)


class InspectionStatus(str, enum.Enum):
    VALID = "valid"
    NEEDS_UPGRADE = "needs_upgrade"
    CORRUPTED = "corrupted"


class PlanAction(str, enum.Enum):
    CREATE_NEW = "CREATE_NEW"  # Ni Source ni Job existen en DB
    CREATE_JOB = "CREATE_JOB"  # Source existe, crear nuevo Job para este chat
    MERGE_REMOTES = "MERGE_REMOTES"  # Job existe pero le faltan piezas remotas
    ALREADY_EXISTS = "ALREADY_EXISTS"  # Totalmente íntegro y sincronizado en DB (No-Op)
    CONFLICT = "CONFLICT"  # Discrepancia en piezas u offsets


@dataclass
class SnapshotInspection:
    path: Path
    status: InspectionStatus
    manifest: Optional[UploadManifest] = None
    detected_version: Optional[str] = None
    error_detail: Optional[str] = None


@dataclass
class PlanItem:
    inspection: SnapshotInspection
    manifest: UploadManifest
    action: PlanAction
    target_chat_id: int
    total_parts: int
    missing_parts_count: int
    existing_source: Optional[Source] = None
    existing_job: Optional[Job] = None
    reason: str = ""


@dataclass
class ImportPlan:
    items: List[PlanItem] = field(default_factory=list)
    needs_upgrade_items: List[SnapshotInspection] = field(default_factory=list)
    corrupted_items: List[SnapshotInspection] = field(default_factory=list)

    @property
    def actionable_items(self) -> List[PlanItem]:
        return [
            i
            for i in self.items
            if i.action
            in (PlanAction.CREATE_NEW, PlanAction.CREATE_JOB, PlanAction.MERGE_REMOTES)
        ]

    @property
    def already_exists_count(self) -> int:
        return sum(1 for i in self.items if i.action == PlanAction.ALREADY_EXISTS)

    @property
    def conflicts_count(self) -> int:
        return sum(1 for i in self.items if i.action == PlanAction.CONFLICT)


class SnapshotInspector:
    @classmethod
    def inspect_file(cls, path: Path) -> SnapshotInspection:
        """
        Inspecciona un archivo de snapshot. Solo acepta nativamente UploadManifest v5.0.
        Si detecta versiones históricas, las marca como NEEDS_UPGRADE.
        """
        try:
            raw_data = load_raw_snapshot(path)
        except Exception as e:
            return SnapshotInspection(
                path=path,
                status=InspectionStatus.CORRUPTED,
                error_detail=f"Error leyendo archivo: {e}",
            )

        if not isinstance(raw_data, dict):
            return SnapshotInspection(
                path=path,
                status=InspectionStatus.CORRUPTED,
                error_detail="El contenido no es un objeto/diccionario JSON/YAML válido.",
            )

        version = SnapshotUpgrader.detect_version(raw_data)

        if version == MANIFEST_VERSION:
            try:
                manifest = UploadManifest.model_validate(raw_data)
                return SnapshotInspection(
                    path=path,
                    status=InspectionStatus.VALID,
                    manifest=manifest,
                    detected_version=version,
                )
            except Exception as e:
                return SnapshotInspection(
                    path=path,
                    status=InspectionStatus.CORRUPTED,
                    error_detail=f"Esquema v{MANIFEST_VERSION} inválido: {e}",
                    detected_version=version,
                )

        return SnapshotInspection(
            path=path,
            status=InspectionStatus.NEEDS_UPGRADE,
            detected_version=version,
        )

    @classmethod
    def find_all_candidates(cls, paths: List[Path]) -> List[Path]:
        """Encuentra recursivamente todos los archivos de snapshots."""
        valid_exts = (".json", ".json.xz", ".yaml", ".yml")
        candidates = []
        for p in paths:
            if p.is_file() and any(p.name.endswith(ext) for ext in valid_exts):
                candidates.append(p)
            elif p.is_dir():
                for sub in p.rglob("*"):
                    if sub.is_file() and any(
                        sub.name.endswith(ext) for ext in valid_exts
                    ):
                        candidates.append(sub)
        return sorted(list(set(candidates)))


class SnapshotImportPlanner:
    @classmethod
    def plan(cls, inspections: List[SnapshotInspection]) -> ImportPlan:
        """
        Evalúa el estado de los snapshots válidos contra la base de datos sin aplicar cambios.
        """
        plan = ImportPlan()

        for insp in inspections:
            if insp.status == InspectionStatus.NEEDS_UPGRADE:
                plan.needs_upgrade_items.append(insp)
                continue
            if insp.status == InspectionStatus.CORRUPTED:
                plan.corrupted_items.append(insp)
                continue

            manifest = insp.manifest
            assert manifest is not None

            target_chat_id = manifest.chat_id
            total_parts = len(manifest.parts)

            existing_source = Source.get_or_none(
                Source.md5sum == manifest.source.md5sum
            )

            if existing_source is None:
                plan.items.append(
                    PlanItem(
                        inspection=insp,
                        manifest=manifest,
                        action=PlanAction.CREATE_NEW,
                        target_chat_id=target_chat_id,
                        total_parts=total_parts,
                        missing_parts_count=total_parts,
                        reason="Nuevo recurso y piezas no registrados en la base de datos.",
                    )
                )
                continue

            existing_job = Job.get_or_none(
                (Job.source == existing_source)
                & (Job.chat == target_chat_id)
                & (Job.deleted_at == 0)
            )

            if existing_job is None:
                plan.items.append(
                    PlanItem(
                        inspection=insp,
                        manifest=manifest,
                        action=PlanAction.CREATE_JOB,
                        target_chat_id=target_chat_id,
                        total_parts=total_parts,
                        missing_parts_count=total_parts,
                        existing_source=existing_source,
                        reason="El recurso ya existe en otro chat. Creando nuevo Job para este chat.",
                    )
                )
                continue

            valid_remotes_count = (
                RemotePayload.select()
                .join(Payload)
                .where(
                    (Payload.job == existing_job) & (RemotePayload.is_orphaned == False)  # noqa: E712
                )
                .count()
            )

            if valid_remotes_count == total_parts:
                plan.items.append(
                    PlanItem(
                        inspection=insp,
                        manifest=manifest,
                        action=PlanAction.ALREADY_EXISTS,
                        target_chat_id=target_chat_id,
                        total_parts=total_parts,
                        missing_parts_count=0,
                        existing_source=existing_source,
                        existing_job=existing_job,
                        reason="Completamente sincronizado e íntegro en la base de datos.",
                    )
                )
            else:
                missing = total_parts - valid_remotes_count
                plan.items.append(
                    PlanItem(
                        inspection=insp,
                        manifest=manifest,
                        action=PlanAction.MERGE_REMOTES,
                        target_chat_id=target_chat_id,
                        total_parts=total_parts,
                        missing_parts_count=missing,
                        existing_source=existing_source,
                        existing_job=existing_job,
                        reason=f"Faltan {missing}/{total_parts} piezas remotas por vincular.",
                    )
                )

        return plan


class SnapshotImporter:
    @classmethod
    def apply_plan(
        cls,
        plan: ImportPlan,
        db: peewee.Database,
        owner: TelegramUser,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> Tuple[int, int]:
        """
        Ejecuta las inserciones atómicas en la DB a partir de los ítems accionables del plan.
        """
        actionable = plan.actionable_items
        succeeded = 0
        failed = 0

        for idx, item in enumerate(actionable, 1):
            try:
                with db_transaction(db):
                    cls._apply_item(item, owner)
                succeeded += 1
            except Exception as e:
                logger.exception(f"Error importando {item.inspection.path.name}: {e}")
                failed += 1

            if on_progress:
                on_progress(idx, len(actionable), item.inspection.path.name)

        return succeeded, failed

    @classmethod
    def _apply_item(cls, item: PlanItem, owner: TelegramUser):
        manifest = item.manifest

        chat, _ = TelegramChat.get_or_create(
            id=item.target_chat_id,
            defaults={
                "title": f"Chat {item.target_chat_id}",
                "type": "channel",
                "is_public": False,
                "last_verified": datetime.now(timezone.utc),
            },
        )

        if item.existing_source:
            source = item.existing_source
        else:
            source = Source.create(
                path_str=manifest.source.filename,
                md5sum=manifest.source.md5sum,
                size=manifest.source.size,
                mtime=manifest.source.mtime,
                mimetype=manifest.source.mime_type,
                type=manifest.source.type,
                tape_catalog=manifest.source.tape_catalog,
            )

        if item.existing_job:
            job = item.existing_job
        else:
            config = StrategyConfig(
                tg_max_size=manifest.chunk_size,
                user_is_premium=False,
                app_version=f"imported-{__VERSION__}",
            )
            job = Job.create(
                source=source,
                chat=chat,
                strategy=manifest.strategy,
                config=config,
                status=JobStatus.UPLOADED,
                deleted_at=0,
            )

        payload_map = {p.sequence_index: p for p in job.payloads}  # type: ignore

        for part in manifest.parts:
            payload = payload_map.get(part.sequence)
            if not payload:
                payload = Payload.create(
                    job=job,
                    sequence_index=part.sequence,
                    filename=part.part_filename,
                    filename_short=part.part_filename,
                    size=part.part_size,
                    start_offset=part.start_offset,
                    end_offset=part.end_offset,
                    md5sum=part.part_md5sum or None,
                )
                payload_map[part.sequence] = payload

            has_remote = (
                RemotePayload.select()
                .where(
                    (RemotePayload.payload == payload)
                    & (RemotePayload.is_orphaned == False)  # noqa: E712
                )
                .exists()
            )

            if not has_remote:
                synthetic_meta = {
                    "id": part.message_id,
                    "chat": {"id": part.chat_id, "type": "channel"},
                    "document": {
                        "file_name": part.part_filename,
                        "file_size": part.part_size,
                        "mime_type": manifest.source.mime_type,
                    },
                }

                RemotePayload.create(
                    payload=payload,
                    message_id=part.message_id,
                    chat_id=part.chat_id,
                    owner=owner,
                    json_metadata=synthetic_meta,
                    last_verified_at=datetime.now(timezone.utc),
                    is_orphaned=False,
                )

        if job.status != JobStatus.UPLOADED:
            job.status = JobStatus.UPLOADED
            job.save(only=[Job.status, Job.updated_at])
