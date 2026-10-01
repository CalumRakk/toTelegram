import enum
import json
import logging
import lzma
import mimetypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
from totelegram.schemas import JobStatus, SourceType, Strategy, StrategyConfig

logger = logging.getLogger(__name__)


# DTOs y Modelos Canónicos Normalizados


class InspectionStatus(str, enum.Enum):
    VALID = "valid"
    UNKNOWN_VERSION = "unknown_version"
    CORRUPTED = "corrupted"


class PlanAction(str, enum.Enum):
    CREATE_NEW = "CREATE_NEW"  # Ni Source ni Job existen
    CREATE_JOB = "CREATE_JOB"  # Source existe, crear nuevo Job para este chat
    MERGE_REMOTES = "MERGE_REMOTES"  # Job existe pero faltaban piezas remotas
    ALREADY_EXISTS = "ALREADY_EXISTS"  # Todo completo en DB (No-Op)
    CONFLICT = "CONFLICT"  # Discrepancia en cantidad de piezas u offsets


@dataclass
class CanonicalPart:
    sequence: int
    filename: str
    filename_short: str
    size: int
    start_offset: int
    end_offset: int
    chat_id: int
    message_id: int
    md5sum: Optional[str] = None


@dataclass
class CanonicalSnapshot:
    filename: str
    size: int
    md5sum: str
    mtime: float
    mimetype: str
    source_type: SourceType
    strategy: Strategy
    parts: List[CanonicalPart]
    raw_version: str
    adapter_name: str


@dataclass
class SnapshotInspection:
    path: Path
    status: InspectionStatus
    canonical: Optional[CanonicalSnapshot] = None
    detected_version: Optional[str] = None
    root_keys: List[str] = field(default_factory=list)
    error_detail: Optional[str] = None


@dataclass
class PlanItem:
    inspection: SnapshotInspection
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
    unknown_items: List[SnapshotInspection] = field(default_factory=list)
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


# Adaptadores de Versión (Normalizadores)


class BaseSnapshotAdapter:
    @staticmethod
    def matches(raw_data: Dict[str, Any]) -> bool:
        raise NotImplementedError

    @classmethod
    def to_canonical(cls, raw_data: Dict[str, Any]) -> CanonicalSnapshot:
        raise NotImplementedError


class LegacyV3Adapter(BaseSnapshotAdapter):
    """
    Soporte para snapshots legacy versión 3.0:
    {"kind": "pieces-file", "manager": {"file": {...}, "pieces": [...]}, "version": "3.0"}
    """

    @staticmethod
    def matches(raw_data: Dict[str, Any]) -> bool:
        return (
            raw_data.get("version") == "3.0"
            and "manager" in raw_data
            and "pieces" in raw_data.get("manager", {})
        )

    @classmethod
    def to_canonical(cls, raw_data: Dict[str, Any]) -> CanonicalSnapshot:
        manager = raw_data["manager"]
        file_info = manager["file"]
        pieces = manager["pieces"]

        # Parseo de fecha
        created_str = raw_data.get("createdTime")
        mtime = datetime.now(timezone.utc).timestamp()
        if created_str:
            try:
                mtime = datetime.fromisoformat(created_str).timestamp()
            except ValueError:
                pass

        # Sanitización de Mimetype (si viene 'idk' o vacío, inferir)
        raw_mime = file_info.get("mimeType", "")
        if not raw_mime or raw_mime.lower() == "idk":
            guessed, _ = mimetypes.guess_type(file_info["filename"])
            mimetype = guessed or "application/octet-stream"
        else:
            mimetype = raw_mime

        # Reconstrucción continua y determinista de offsets
        parts: List[CanonicalPart] = []
        current_offset = 0

        for idx, piece in enumerate(pieces):
            p_size = piece["size"]
            msg = piece.get("message", {})

            start = current_offset
            end = current_offset + p_size
            current_offset = end

            part = CanonicalPart(
                sequence=idx,
                filename=piece["filename"],
                filename_short=msg.get("file_name", piece["filename"]),
                size=p_size,
                start_offset=start,
                end_offset=end,
                chat_id=msg["chat_id"],
                message_id=msg["message_id"],
                md5sum=None,
            )
            parts.append(part)

        strategy = Strategy.CHUNKED if len(parts) > 1 else Strategy.SINGLE

        return CanonicalSnapshot(
            filename=file_info["filename"],
            size=file_info["size"],
            md5sum=file_info["md5sum"],
            mtime=mtime,
            mimetype=mimetype,
            source_type=SourceType.FILE,
            strategy=strategy,
            parts=parts,
            raw_version="3.0",
            adapter_name="LegacyV3Adapter",
        )


class ModernCanonicalAdapter(BaseSnapshotAdapter):
    """
    Soporte para snapshots modernos de toTelegram (UploadManifest actual):
    {"source": {"filename": ..., "md5sum": ...}, "parts": [...], "strategy": ...}
    """

    @staticmethod
    def matches(raw_data: Dict[str, Any]) -> bool:
        return "source" in raw_data and "parts" in raw_data

    @classmethod
    def to_canonical(cls, raw_data: Dict[str, Any]) -> CanonicalSnapshot:
        src = raw_data["source"]
        raw_parts = raw_data["parts"]

        source_type = SourceType(src.get("type", "file"))
        strategy = Strategy(raw_data.get("strategy", "single-file"))

        parts: List[CanonicalPart] = []
        current_offset = 0

        for idx, p in enumerate(raw_parts):
            p_size = p.get("part_size", p.get("size", 0))
            start = p.get("start_offset", current_offset)
            end = p.get("end_offset", start + p_size)
            current_offset = end

            part = CanonicalPart(
                sequence=p.get("sequence", idx),
                filename=p.get("part_filename", p.get("filename", "")),
                filename_short=p.get(
                    "part_filename_short", p.get("filename_short", "")
                ),
                size=p_size,
                start_offset=start,
                end_offset=end,
                chat_id=p["chat_id"],
                message_id=p["message_id"],
                md5sum=p.get("part_md5sum", p.get("md5sum")),
            )
            parts.append(part)

        return CanonicalSnapshot(
            filename=src.get("filename", ""),
            size=src.get("size", sum(p.size for p in parts)),
            md5sum=src.get("md5sum", ""),
            mtime=src.get("mtime", datetime.now(timezone.utc).timestamp()),
            mimetype=src.get("mimetype", "application/octet-stream"),
            source_type=source_type,
            strategy=strategy,
            parts=parts,
            raw_version=str(raw_data.get("version", "modern")),
            adapter_name="ModernCanonicalAdapter",
        )


# Inspector


class SnapshotInspector:
    ADAPTERS = [LegacyV3Adapter, ModernCanonicalAdapter]

    @classmethod
    def load_raw_json(cls, path: Path) -> Dict[str, Any]:
        """Carga transparente de archivos .json o .json.xz."""
        if path.suffix == ".xz" or path.name.endswith(".json.xz"):
            with lzma.open(path, "rt", encoding="utf-8") as f:
                return json.load(f)
        else:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    @classmethod
    def inspect_file(cls, path: Path) -> SnapshotInspection:
        try:
            raw_data = cls.load_raw_json(path)
        except Exception as e:
            return SnapshotInspection(
                path=path,
                status=InspectionStatus.CORRUPTED,
                error_detail=f"Fallo de lectura/descompresión: {e}",
            )

        if not isinstance(raw_data, dict):
            return SnapshotInspection(
                path=path,
                status=InspectionStatus.CORRUPTED,
                error_detail="El contenido raíz no es un objeto JSON válido.",
            )

        root_keys = sorted(list(raw_data.keys()))
        detected_version = str(raw_data.get("version", "desconocida"))

        # Probar adaptadores registrados
        for adapter in cls.ADAPTERS:
            if adapter.matches(raw_data):
                try:
                    canonical = adapter.to_canonical(raw_data)
                    return SnapshotInspection(
                        path=path,
                        status=InspectionStatus.VALID,
                        canonical=canonical,
                        detected_version=canonical.raw_version,
                        root_keys=root_keys,
                    )
                except Exception as e:
                    return SnapshotInspection(
                        path=path,
                        status=InspectionStatus.CORRUPTED,
                        error_detail=f"Error en adaptador {adapter.__name__}: {e}",
                        detected_version=detected_version,
                        root_keys=root_keys,
                    )

        # Si ningún adaptador pudo interpretarlo:
        return SnapshotInspection(
            path=path,
            status=InspectionStatus.UNKNOWN_VERSION,
            detected_version=detected_version,
            root_keys=root_keys,
        )

    @classmethod
    def find_all_candidates(cls, paths: List[Path]) -> List[Path]:
        """Encuentra recursivamente todos los .json y .json.xz."""
        candidates = []
        for p in paths:
            if p.is_file():
                if p.name.endswith(".json") or p.name.endswith(".json.xz"):
                    candidates.append(p)
            elif p.is_dir():
                for sub in p.rglob("*"):
                    if sub.is_file() and (
                        sub.name.endswith(".json") or sub.name.endswith(".json.xz")
                    ):
                        candidates.append(sub)
        return sorted(list(set(candidates)))


# Planificador de Base de Datos (Solo Lectura)


class SnapshotImportPlanner:
    @classmethod
    def plan(cls, inspections: List[SnapshotInspection]) -> ImportPlan:
        plan = ImportPlan()

        for insp in inspections:
            if insp.status == InspectionStatus.UNKNOWN_VERSION:
                plan.unknown_items.append(insp)
                print(f"Unknown version: {insp.path}")
                continue
            if insp.status == InspectionStatus.CORRUPTED:
                plan.corrupted_items.append(insp)
                continue

            canonical = insp.canonical
            assert canonical is not None

            # Determinar chat_id objetivo
            if not canonical.parts:
                plan.corrupted_items.append(
                    SnapshotInspection(
                        path=insp.path,
                        status=InspectionStatus.CORRUPTED,
                        error_detail="El snapshot no contiene piezas registradas.",
                    )
                )
                continue

            target_chat_id = canonical.parts[0].chat_id
            total_parts = len(canonical.parts)

            # 1. ¿Existe el Source por MD5?
            existing_source = Source.get_or_none(Source.md5sum == canonical.md5sum)

            if existing_source is None:
                plan.items.append(
                    PlanItem(
                        inspection=insp,
                        action=PlanAction.CREATE_NEW,
                        target_chat_id=target_chat_id,
                        total_parts=total_parts,
                        missing_parts_count=total_parts,
                        reason="Nuevo contenido y nuevo Job en la base de datos.",
                    )
                )
                continue

            # 2. Source existe. ¿Existe Job activo para este chat?
            existing_job = Job.get_or_none(
                (Job.source == existing_source)
                & (Job.chat == target_chat_id)
                & (Job.deleted_at == 0)
            )

            if existing_job is None:
                plan.items.append(
                    PlanItem(
                        inspection=insp,
                        action=PlanAction.CREATE_JOB,
                        target_chat_id=target_chat_id,
                        total_parts=total_parts,
                        missing_parts_count=total_parts,
                        existing_source=existing_source,
                        reason="Source existente detectado. Creando nuevo Job para este Chat.",
                    )
                )
                continue

            # 3. Source y Job existen. Comparar piezas remotas activas
            existing_payloads = list(
                existing_job.payloads.order_by(Payload.sequence_index)
            )
            if existing_payloads and len(existing_payloads) != total_parts:
                plan.items.append(
                    PlanItem(
                        inspection=insp,
                        action=PlanAction.CONFLICT,
                        target_chat_id=target_chat_id,
                        total_parts=total_parts,
                        missing_parts_count=0,
                        existing_source=existing_source,
                        existing_job=existing_job,
                        reason=f"Conflicto de piezas: DB tiene {len(existing_payloads)} partes, snapshot tiene {total_parts}.",
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
                        action=PlanAction.ALREADY_EXISTS,
                        target_chat_id=target_chat_id,
                        total_parts=total_parts,
                        missing_parts_count=0,
                        existing_source=existing_source,
                        existing_job=existing_job,
                        reason="Íntegro y completo en base de datos.",
                    )
                )
            else:
                missing = total_parts - valid_remotes_count
                plan.items.append(
                    PlanItem(
                        inspection=insp,
                        action=PlanAction.MERGE_REMOTES,
                        target_chat_id=target_chat_id,
                        total_parts=total_parts,
                        missing_parts_count=missing,
                        existing_source=existing_source,
                        existing_job=existing_job,
                        reason=f"Job existente pero faltan {missing}/{total_parts} piezas remotas.",
                    )
                )

        return plan


# Ejecutor Transaccional


class SnapshotImporter:
    @classmethod
    def apply_plan(
        cls,
        plan: ImportPlan,
        db: peewee.Database,
        owner: TelegramUser,
        on_progress=None,
    ) -> Tuple[int, int]:
        """
        Ejecuta las inserciones atómicas por cada snapshot.
        Retorna (exitosos, fallidos).
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
        canonical = item.inspection.canonical
        assert canonical is not None

        # 1. Asegurar TelegramChat (crea un stub si no existe en la DB receptora)
        chat, _ = TelegramChat.get_or_create(
            id=item.target_chat_id,
            defaults={
                "title": f"Chat {item.target_chat_id}",
                "type": "channel",
                "is_public": False,
                "last_verified": datetime.now(timezone.utc),
            },
        )

        # 2. Resolver o Crear Source
        if item.existing_source:
            source = item.existing_source
        else:
            source = Source.create(
                path_str=canonical.filename,
                md5sum=canonical.md5sum,
                size=canonical.size,
                mtime=canonical.mtime,
                mimetype=canonical.mimetype,
                type=canonical.source_type,
            )

        # 3. Resolver o Crear Job
        if item.existing_job:
            job = item.existing_job
        else:
            max_part_size = max(
                (p.size for p in canonical.parts), default=2000 * 1024 * 1024
            )
            config = StrategyConfig(
                tg_max_size=max_part_size,
                user_is_premium=False,
                app_version=f"imported-{__VERSION__}",
            )
            job = Job.create(
                source=source,
                chat=chat,
                strategy=canonical.strategy,
                config=config,
                status=JobStatus.UPLOADED,
                deleted_at=0,
            )

        # 4. Crear o Completar Payloads y RemotePayloads
        payload_map = {p.sequence_index: p for p in job.payloads}  # type: ignore

        for part in canonical.parts:
            payload = payload_map.get(part.sequence)
            if not payload:
                payload = Payload.create(
                    job=job,
                    sequence_index=part.sequence,
                    filename=part.filename,
                    filename_short=part.filename_short,
                    size=part.size,
                    start_offset=part.start_offset,
                    end_offset=part.end_offset,
                    md5sum=part.md5sum,
                )
                payload_map[part.sequence] = payload

            # Verificar si ya tiene RemotePayload activo
            has_remote = (
                RemotePayload.select()
                .where(
                    (RemotePayload.payload == payload)
                    & (RemotePayload.is_orphaned == False)  # noqa: E712
                )
                .exists()
            )

            if not has_remote:
                # Estructura JSON sintética compatible con pyrogram/client.py
                synthetic_meta = {
                    "id": part.message_id,
                    "chat": {"id": part.chat_id, "type": "channel"},
                    "document": {
                        "file_name": part.filename_short,
                        "file_size": part.size,
                        "mime_type": canonical.mimetype,
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
