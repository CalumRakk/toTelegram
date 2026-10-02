import enum
import json
import logging
import lzma
import mimetypes
import re
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
    NEEDS_TELEGRAM = "NEEDS_TELEGRAM"


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
    is_incomplete: bool = False


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
    Soporte para snapshots legacy versión 3.0.
    Maneja tanto archivos troceados ('pieces-file') como archivos únicos ('single-file').
    """

    @staticmethod
    def matches(raw_data: Dict[str, Any]) -> bool:
        if raw_data.get("version") != "3.0" or "manager" not in raw_data:
            return False
        mgr = raw_data.get("manager", {})
        # Hace match si tiene la lista de pieces O el mensaje directo de single-file
        return "pieces" in mgr or "message" in mgr

    @classmethod
    def to_canonical(cls, raw_data: Dict[str, Any]) -> CanonicalSnapshot:
        manager = raw_data["manager"]
        file_info = manager["file"]
        is_single = "message" in manager or raw_data.get("kind") == "single-file"

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

        parts: List[CanonicalPart] = []

        # Archivo Único (single-file)
        if is_single:
            msg = manager["message"]
            file_size = file_info["size"]
            part = CanonicalPart(
                sequence=0,
                filename=file_info["filename"],
                filename_short=msg.get("file_name", file_info["filename"]),
                size=file_size,
                start_offset=0,
                end_offset=file_size,
                chat_id=msg["chat_id"],
                message_id=msg["message_id"],
                md5sum=file_info["md5sum"],  # En archivo único, el hash coincide
            )
            parts.append(part)
            strategy = Strategy.SINGLE

        # Archivo Troceado (pieces-file)
        else:
            pieces = manager.get("pieces", [])
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


class LegacyYamlV0Adapter(BaseSnapshotAdapter):
    """
    Soporte para snapshots ancestrales v0.0.1 en formato YAML:
    chat_id: -100...
    md5sum: aa0b...
    version: 0.0.1
    files:
      - filename: '...mp4_1-3'
        message_id: 169
        part: 1-3
        size: 2097152000
    """

    @staticmethod
    def matches(raw_data: Dict[str, Any]) -> bool:
        return (
            isinstance(raw_data, dict)
            and "files" in raw_data
            and "chat_id" in raw_data
            and "md5sum" in raw_data
            and str(raw_data.get("version", "")).startswith("0.0.")
        )

    @classmethod
    def to_canonical(cls, raw_data: Dict[str, Any]) -> CanonicalSnapshot:
        files = raw_data["files"]
        target_chat_id = int(raw_data["chat_id"])
        md5sum = str(raw_data["md5sum"])

        # Deducir nombre base original limpiando el sufijo '_1-3' o '_X-Y'
        first_filename = files[0]["filename"]
        base_filename = re.sub(r"_\d+-\d+$", "", first_filename)

        # Inferir MimeType a partir de la extensión del nombre recuperado
        guessed_mime, _ = mimetypes.guess_type(base_filename)
        mimetype = guessed_mime or "application/octet-stream"

        # Reconstruir partes y calcular offsets continuos
        parts: List[CanonicalPart] = []
        current_offset = 0

        for idx, item in enumerate(files):
            p_size = int(item["size"])
            start = current_offset
            end = current_offset + p_size
            current_offset = end

            # Parsear secuencia si viene como '1-3'
            part_str = str(item.get("part", ""))
            if "-" in part_str:
                try:
                    seq = int(part_str.split("-")[0]) - 1
                except ValueError:
                    seq = idx
            else:
                seq = idx

            part_md5 = md5sum if len(files) == 1 else None
            part = CanonicalPart(
                sequence=seq,
                filename=item["filename"],
                filename_short=item["filename"],
                size=p_size,
                start_offset=start,
                end_offset=end,
                chat_id=target_chat_id,
                message_id=int(item["message_id"]),
                md5sum=part_md5,
            )
            parts.append(part)

        total_size = current_offset
        strategy = Strategy.CHUNKED if len(parts) > 1 else Strategy.SINGLE

        return CanonicalSnapshot(
            filename=base_filename,
            size=total_size,
            md5sum=md5sum,
            mtime=datetime.now(timezone.utc).timestamp(),
            mimetype=mimetype,
            source_type=SourceType.FILE,
            strategy=strategy,
            parts=parts,
            raw_version=str(raw_data.get("version", "0.0.1")),
            adapter_name="LegacyYamlV0Adapter",
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


class LegacyDocumentsYamlAdapter(BaseSnapshotAdapter):
    """
    Soporta formato ancestral sin versión ni tamaños.
    Delega la resolución de tamaños exactos a Telegram.
    """

    @staticmethod
    def matches(raw_data: Dict[str, Any]) -> bool:
        if not isinstance(raw_data, dict):
            return False
        return (
            "filedocuments" in raw_data
            and "md5sum" in raw_data
            and "chat_id" in raw_data
        )

    @classmethod
    def to_canonical(cls, raw_data: Dict[str, Any]) -> CanonicalSnapshot:
        docs = raw_data["filedocuments"]
        chat_id = int(raw_data["chat_id"])
        md5sum = str(raw_data["md5sum"])

        first_name = docs[0]["filepart"]
        base_filename = re.sub(r"_\d+-\d+$", "", first_name)

        guessed_mime, _ = mimetypes.guess_type(base_filename)
        mimetype = guessed_mime or "application/octet-stream"

        parts: List[CanonicalPart] = []
        has_missing_sizes = False

        for idx, doc in enumerate(docs):
            msg_id = int(doc["message_id"])
            p_name = doc["filepart"]

            # Si el documento no tiene 'size', marcamos -1
            if "size" in doc:
                p_size = int(doc["size"])
            else:
                p_size = -1
                has_missing_sizes = True

            parts.append(
                CanonicalPart(
                    sequence=idx,
                    filename=p_name,
                    filename_short=p_name,
                    size=p_size,
                    start_offset=0,
                    end_offset=0,
                    chat_id=chat_id,
                    message_id=msg_id,
                    md5sum=None,
                )
            )

        strategy = Strategy.CHUNKED if len(parts) > 1 else Strategy.SINGLE

        return CanonicalSnapshot(
            filename=base_filename,
            size=0,
            md5sum=md5sum,
            mtime=datetime.now(timezone.utc).timestamp(),
            mimetype=mimetype,
            source_type=SourceType.FILE,
            strategy=strategy,
            parts=parts,
            raw_version="pre-alpha (filedocuments)",
            adapter_name="LegacyDocumentsYamlAdapter",
            is_incomplete=has_missing_sizes,
        )


# Inspector


class SnapshotInspector:
    ADAPTERS = [LegacyV3Adapter, LegacyYamlV0Adapter, ModernCanonicalAdapter]

    @classmethod
    def load_raw_data(cls, path: Path) -> Dict[str, Any]:
        """Carga transparente de archivos .json, .json.xz, .yaml y .yml."""
        # Caso YAML
        if path.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml

                class SafeObjectLoader(yaml.SafeLoader):
                    pass

                def _construct_python_object(loader, tag_suffix, node):
                    return loader.construct_mapping(node)

                SafeObjectLoader.add_multi_constructor(
                    "tag:yaml.org,2002:python/object:", _construct_python_object
                )
                SafeObjectLoader.add_multi_constructor(
                    "!python/object:", _construct_python_object
                )

            except ImportError:
                raise RuntimeError(
                    "PyYAML no está instalado. Ejecuta: pip install pyyaml"
                )
            with open(path, "r", encoding="utf-8") as f:
                # Usar SafeObjectLoader para neutralizar tags !!python/object:...
                return yaml.load(f, Loader=SafeObjectLoader)

        # Caso JSON comprimido XZ
        if path.suffix == ".xz" or path.name.endswith(".json.xz"):
            with lzma.open(path, "rt", encoding="utf-8") as f:
                return json.load(f)

        # Caso JSON plano
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @classmethod
    def inspect_file(cls, path: Path) -> SnapshotInspection:
        try:
            raw_data = cls.load_raw_data(path)
        except Exception as e:
            return SnapshotInspection(
                path=path,
                status=InspectionStatus.CORRUPTED,
                error_detail=f"Fallo de lectura/parser: {e}",
            )

        if not isinstance(raw_data, dict):
            return SnapshotInspection(
                path=path,
                status=InspectionStatus.CORRUPTED,
                error_detail="El contenido raíz no es un diccionario/objeto válido.",
            )

        root_keys = sorted(list(raw_data.keys()))
        detected_version = str(raw_data.get("version", "desconocida"))

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

        return SnapshotInspection(
            path=path,
            status=InspectionStatus.UNKNOWN_VERSION,
            detected_version=detected_version,
            root_keys=root_keys,
        )

    @classmethod
    def find_all_candidates(cls, paths: List[Path]) -> List[Path]:
        """Encuentra recursivamente todos los .json, .json.xz, .yaml y .yml."""
        valid_exts = (".json", ".json.xz", ".yaml", ".yml")
        candidates = []
        for p in paths:
            if p.is_file():
                if any(p.name.endswith(ext) for ext in valid_exts):
                    candidates.append(p)
            elif p.is_dir():
                for sub in p.rglob("*"):
                    if sub.is_file() and any(
                        sub.name.endswith(ext) for ext in valid_exts
                    ):
                        candidates.append(sub)
        return sorted(list(set(candidates)))


# Planificador de Base de Datos (Solo Lectura)


class SnapshotImportPlanner:
    @classmethod
    def plan(cls, inspections: List[SnapshotInspection], client=None) -> ImportPlan:
        plan = ImportPlan()

        for insp in inspections:
            if insp.status == InspectionStatus.UNKNOWN_VERSION:
                plan.unknown_items.append(insp)
                continue
            if insp.status == InspectionStatus.CORRUPTED:
                plan.corrupted_items.append(insp)
                continue

            canonical = insp.canonical
            assert canonical is not None

            # AUTO-CURACIÓN ONLINE: Si faltan tamaños, preguntar a Telegram
            if canonical.is_incomplete:
                if client is None:
                    plan.items.append(
                        PlanItem(
                            inspection=insp,
                            action=PlanAction.NEEDS_TELEGRAM,
                            target_chat_id=canonical.parts[0].chat_id,
                            total_parts=len(canonical.parts),
                            missing_parts_count=len(canonical.parts),
                            reason="Faltan tamaños de piezas. Requiere ejecutar con sesión de Telegram activa.",
                        )
                    )
                    continue

                # Curar llamando a Telegram
                try:
                    cls._heal_from_telegram(client, canonical)
                except Exception as e:
                    plan.items.append(
                        PlanItem(
                            inspection=insp,
                            action=PlanAction.CONFLICT,
                            target_chat_id=canonical.parts[0].chat_id,
                            total_parts=len(canonical.parts),
                            missing_parts_count=0,
                            reason=f"No se pudo verificar en Telegram: {e}",
                        )
                    )
                    continue

            # (A partir de aquí, canonical tiene tamaños y offsets 100% exactos)
            target_chat_id = canonical.parts[0].chat_id
            total_parts = len(canonical.parts)

            existing_source = Source.get_or_none(Source.md5sum == canonical.md5sum)

            if existing_source is None:
                plan.items.append(
                    PlanItem(
                        inspection=insp,
                        action=PlanAction.CREATE_NEW,
                        target_chat_id=target_chat_id,
                        total_parts=total_parts,
                        missing_parts_count=total_parts,
                        reason="Nuevo contenido curado vía Telegram.",
                    )
                )
                continue

            # (El resto de la lógica de Source/Job existente sigue idéntica...)
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

            # Verificación de piezas existentes
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

    @classmethod
    def _heal_from_telegram(cls, client, canonical: CanonicalSnapshot):
        """Consulta los mensajes reales a Telegram para obtener tamaños y nombres exactos."""
        # TODO: ver si moverlo al paquete telegram.
        chat_id = canonical.parts[0].chat_id
        msg_ids = [p.message_id for p in canonical.parts]

        messages = client.get_messages(chat_id, msg_ids)
        if not isinstance(messages, list):
            messages = [messages]

        msg_map = {m.id: m for m in messages if m and not getattr(m, "empty", True)}

        current_offset = 0
        for part in canonical.parts:
            msg = msg_map.get(part.message_id)
            if not msg or not msg.document:
                raise ValueError(
                    f"Mensaje {part.message_id} en chat {chat_id} no existe o no tiene documento."
                )

            real_size = msg.document.file_size
            part.size = real_size
            part.filename_short = msg.document.file_name or part.filename_short
            part.start_offset = current_offset
            part.end_offset = current_offset + real_size
            current_offset = part.end_offset

        canonical.size = current_offset
        canonical.is_incomplete = False


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

        # Asegurar TelegramChat (crea un stub si no existe en la DB receptora)
        chat, _ = TelegramChat.get_or_create(
            id=item.target_chat_id,
            defaults={
                "title": f"Chat {item.target_chat_id}",
                "type": "channel",
                "is_public": False,
                "last_verified": datetime.now(timezone.utc),
            },
        )

        # Resolver o Crear Source
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

        # Resolver o Crear Job
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

        # Crear o Completar Payloads y RemotePayloads
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
