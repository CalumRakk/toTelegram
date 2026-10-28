import enum
import json
import logging
import lzma
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Tuple, cast

from totelegram.packaging.publisher import SnapshotPublicationError, SnapshotPublisher
from totelegram.telegram.captions import ManifestCaption

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Message

import peewee

from totelegram.common.helpers import get_utc_now
from totelegram.database import db_transaction
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TapeMember,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.schemas import (
    MANIFEST_VERSION,
    RemotePart,
    SourceMetadata,
    UploadManifest,
)
from totelegram.packaging.ttape import TapeContainer
from totelegram.schemas import SourceType

logger = logging.getLogger(__name__)

# ESTADOS ATÓMICOS DE INSPECCIÓN Y PIEZAS


class InspectionStatus(str, enum.Enum):
    VALID = "valid"  # UploadManifest v6.0 íntegro
    NEEDS_UPGRADE = "needs_upgrade"  # Versión anterior incompatible (< 6.0)
    AHEAD = "ahead"  # Versión más reciente que requiere actualizar el cliente (> 6.0)
    CORRUPTED = "corrupted"  # Archivo corrupto o JSON/ZIP inválido


class PartAction(str, enum.Enum):
    """Acción atómica para cada fragmento físico en Telegram."""

    ALREADY_EXISTS = "already_exists"  # Ya registrado en BD con este message_id
    WILL_LINK = "will_link"  # Nueva parte remota a registrar
    CONFLICT = "conflict"  # Discrepancia física (offsets o hashes)


class PlanAction(str, enum.Enum):
    """Acción macro sobre el recurso en la Base de Datos."""

    CREATE_NEW = "CREATE_NEW"  # Recurso y Job completamente nuevos
    CREATE_JOB = "CREATE_JOB"  # Source existente, nuevo Job en este chat
    MERGE_REMOTES = (
        "MERGE_REMOTES"  # Job existente, se completan piezas faltantes o inventario
    )
    ALREADY_EXISTS = "ALREADY_EXISTS"  # Todo al día (se vuelve estadística)
    CONFLICT = "CONFLICT"  # Discrepancia insalvable en el particionado


# DTOs DEL DIFERENCIAL Y MUTACIONES


@dataclass
class RehydrationResult:
    """Resultado consolidado de una operación de rehidratación desde Telegram."""

    succeeded_count: int
    total_processed: int
    errors: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        """Indica si al menos un recurso fue rehidratado sin errores."""
        return self.succeeded_count > 0 and len(self.errors) == 0

    @property
    def has_changes(self) -> bool:
        """Indica si hubo mutaciones efectivas en la base de datos."""
        return self.succeeded_count > 0


@dataclass
class PartDiff:
    """Auditoría individual de una pieza contra la base de datos."""

    sequence: int
    total_parts: int
    message_id: int
    part_filename: str
    part_size: int
    action: PartAction
    detail: str = ""


@dataclass
class DbMutations:
    """Describe con precisión quirúrgica qué registros se escribirán en BD."""

    create_source: bool = False
    create_job: bool = False
    payloads_count: int = 0
    remotes_count: int = 0
    members_count: int = 0  # Cantidad real de archivos a indexar si es carpeta TAR

    @property
    def has_changes(self) -> bool:
        """Determina si este plan alterará la base de datos."""
        return (
            self.create_source
            or self.create_job
            or self.payloads_count > 0
            or self.remotes_count > 0
            or self.members_count > 0
        )

    def to_badge(self) -> str:
        """Genera la representación compacta para la columna 'Operaciones en BD'."""
        ops = []
        if self.create_source:
            ops.append("+Sour")
        if self.create_job:
            ops.append("+Job")
        if self.payloads_count > 0:
            ops.append(f"+{self.payloads_count}Pays")
        if self.remotes_count > 0:
            ops.append(f"+{self.remotes_count}Res")
        if self.members_count > 0:
            ops.append(f"+{self.members_count}Fils")
        return "  ".join(ops) if ops else "[dim]Sin cambios[/]"


@dataclass
class SnapshotInspection:
    path: Path
    status: InspectionStatus
    manifest: UploadManifest | None = None
    detected_version: str | None = None
    error_detail: str | None = None
    message_id: int | None = None


@dataclass
class PlanItem:
    """Representa el balance de un archivo individual tras cruzarlo con la BD."""

    inspection: SnapshotInspection
    manifest: UploadManifest
    action: PlanAction
    target_chat_id: int
    total_parts: int
    mutations: DbMutations
    parts_diff: List[PartDiff] = field(default_factory=list)
    existing_source: Source | None = None
    existing_job: Job | None = None
    reason: str = ""

    @property
    def is_partial(self) -> bool:
        """Indica si el recurso tiene una mezcla de partes existentes y nuevas."""
        has_existing = any(
            p.action == PartAction.ALREADY_EXISTS for p in self.parts_diff
        )
        has_new = any(p.action == PartAction.WILL_LINK for p in self.parts_diff)
        return (has_existing and has_new) or (
            self.mutations.members_count > 0 and has_existing
        )

    @property
    def missing_parts_count(self) -> int:
        return self.mutations.remotes_count


@dataclass
class ImportPlan:
    # Silenciosos: completamente al día (solo suman a la estadística)
    synced_count: int = 0

    # Accionables: los que tienen mutaciones reales y van a la tabla
    items: List[PlanItem] = field(default_factory=list)

    # Conflictos: discrepancias estructurales que bloquean la importación
    conflicts: List[PlanItem] = field(default_factory=list)

    # Atención requerida externa
    needs_upgrade_items: List[SnapshotInspection] = field(default_factory=list)
    ahead_items: List[SnapshotInspection] = field(default_factory=list)
    corrupted_items: List[SnapshotInspection] = field(default_factory=list)

    @property
    def total_scanned(self) -> int:
        return (
            self.synced_count
            + len(self.items)
            + len(self.conflicts)
            + len(self.needs_upgrade_items)
            + len(self.ahead_items)
            + len(self.corrupted_items)
        )

    @property
    def actionable_items(self) -> List[PlanItem]:
        """Solo aquellos que realmente escribirán en la base de datos."""
        return [i for i in self.items if i.mutations.has_changes]

    @property
    def partial_items(self) -> List[PlanItem]:
        """Aquellos que tienen piezas mixtas (alimentarán el árbol de detalles)."""
        return [i for i in self.items if i.is_partial]


def _parse_version_tuple(ver_str: str) -> tuple[int, ...]:
    """Convierte cadenas como '6.0', 'v7.1' o '6' en tuplas comparables (6, 0)."""
    try:
        nums = re.findall(r"\d+", str(ver_str))
        return tuple(map(int, nums)) if nums else (0,)
    except Exception:
        return (0,)


class SnapshotInspector:
    """Inspecciona y valida archivos snapshot v6.0 (.ttape y .json.xz)."""

    @classmethod
    def inspect_file(cls, path: Path) -> SnapshotInspection:
        manifest: UploadManifest | None = None
        raw_version: str | None = None

        # Contenedor híbrido .ttape (Carpetas)
        if path.suffix == ".ttape" or path.name.endswith(".ttape"):
            try:
                if not TapeContainer.is_valid(path):
                    return SnapshotInspection(
                        path=path,
                        status=InspectionStatus.CORRUPTED,
                        error_detail="Contenedor .ttape inválido o faltan artefactos requeridos.",
                    )
                manifest = TapeContainer.read_manifest(path)
                raw_version = manifest.manifest_version
            except Exception as e:
                return SnapshotInspection(
                    path=path,
                    status=InspectionStatus.CORRUPTED,
                    error_detail=f"Error leyendo contenedor .ttape: {e}",
                )

        # Snapshot de archivo individual (.json.xz)
        elif path.suffix == ".xz" or path.name.endswith(".json.xz"):
            try:
                with lzma.open(path, "rt", encoding="utf-8") as f:
                    raw_data = json.load(f)
            except Exception as e:
                return SnapshotInspection(
                    path=path,
                    status=InspectionStatus.CORRUPTED,
                    error_detail=f"Error descomprimiendo archivo .json.xz: {e}",
                )

            if not isinstance(raw_data, dict):
                return SnapshotInspection(
                    path=path,
                    status=InspectionStatus.CORRUPTED,
                    error_detail="El contenido no es un objeto JSON válido.",
                )

            raw_version = str(raw_data.get("manifest_version", "unknown"))
            if raw_version == MANIFEST_VERSION:
                try:
                    manifest = UploadManifest.model_validate(raw_data)
                except Exception as e:
                    return SnapshotInspection(
                        path=path,
                        status=InspectionStatus.CORRUPTED,
                        error_detail=f"Esquema v{MANIFEST_VERSION} inválido: {e}",
                        detected_version=raw_version,
                    )
        else:
            return SnapshotInspection(
                path=path,
                status=InspectionStatus.CORRUPTED,
                error_detail="Extensión no reconocida. Se requiere .ttape o .json.xz.",
            )

        # Validación comparativa de versión para cualquier formato
        target_tuple = _parse_version_tuple(MANIFEST_VERSION)
        detected_tuple = _parse_version_tuple(raw_version or "0")

        if detected_tuple == target_tuple and manifest is not None:
            return SnapshotInspection(
                path=path,
                status=InspectionStatus.VALID,
                manifest=manifest,
                detected_version=raw_version,
            )

        if detected_tuple > target_tuple:
            return SnapshotInspection(
                path=path,
                status=InspectionStatus.AHEAD,
                detected_version=raw_version,
                error_detail=(
                    f"Versión '{raw_version}' es más reciente que la soportada por este cliente (v{MANIFEST_VERSION}). "
                    "Actualiza toTelegram: pip install --upgrade totelegram"
                ),
            )

        return SnapshotInspection(
            path=path,
            status=InspectionStatus.NEEDS_UPGRADE,
            detected_version=raw_version,
            error_detail=(
                f"Versión '{raw_version}' es un formato anterior incompatible con este estándar (v{MANIFEST_VERSION})."
            ),
        )

    @classmethod
    def find_all_candidates(cls, paths: List[Path]) -> List[Path]:
        valid_exts = (".json.xz", ".ttape")
        candidates = []
        for p in paths:
            if p.is_file() and any(p.name.lower().endswith(ext) for ext in valid_exts):
                candidates.append(p)
            elif p.is_dir():
                for sub in p.rglob("*"):
                    if sub.is_file() and any(
                        sub.name.lower().endswith(ext) for ext in valid_exts
                    ):
                        candidates.append(sub)
        return sorted(set(candidates))


class SnapshotImportPlanner:
    """Calcula un plan determinista de importación cruzando los snapshots con el estado actual de la BD."""

    @classmethod
    def plan(cls, inspections: List[SnapshotInspection]) -> ImportPlan:
        plan = ImportPlan()

        planned_sources_md5: set[str] = set()
        planned_jobs: set[Tuple[str, int]] = set()

        for insp in inspections:
            if insp.status == InspectionStatus.NEEDS_UPGRADE:
                plan.needs_upgrade_items.append(insp)
                continue
            if insp.status == InspectionStatus.AHEAD:
                plan.ahead_items.append(insp)
                continue
            if insp.status == InspectionStatus.CORRUPTED:
                plan.corrupted_items.append(insp)
                continue

            manifest = insp.manifest
            assert manifest is not None

            target_chat_id = manifest.chat_id
            total_parts = len(manifest.parts)
            md5 = manifest.source.md5sum
            job_key = (md5, target_chat_id)

            if job_key in planned_jobs:
                plan.synced_count += 1
                continue

            existing_source = Source.get_or_none(Source.md5sum == md5)
            source_exists = existing_source is not None or md5 in planned_sources_md5

            existing_job: Job | None = None
            if existing_source:
                existing_job = Job.get_for_source_in_chat(
                    existing_source,
                    target_chat_id,  # type: ignore
                )

            # Auditoría de archivos de cinta TAR: en v6.0 proviene exclusivamente de tape_catalog
            is_folder = manifest.source.type == SourceType.FOLDER
            total_inventory = (
                manifest.source.tape_catalog.total_files
                if (is_folder and manifest.source.tape_catalog)
                else 0
            )

            existing_members_count = 0
            if existing_source:
                existing_members_count = (
                    TapeMember.select()
                    .where(TapeMember.source == existing_source)
                    .count()
                )
            elif md5 in planned_sources_md5:
                existing_members_count = total_inventory

            missing_members = (
                max(0, total_inventory - existing_members_count) if is_folder else 0
            )

            # Recurso completamente nuevo o Job no existente en este chat
            if existing_job is None:
                create_source = not source_exists
                create_job = True

                parts_diff = [
                    PartDiff(
                        sequence=p.sequence,
                        total_parts=total_parts,
                        message_id=p.message_id,
                        part_filename=p.part_filename,
                        part_size=p.part_size,
                        action=PartAction.WILL_LINK,
                        detail="Nueva parte a registrar",
                    )
                    for p in manifest.parts
                ]

                mutations = DbMutations(
                    create_source=create_source,
                    create_job=create_job,
                    payloads_count=total_parts,
                    remotes_count=total_parts,
                    members_count=missing_members,
                )

                action = (
                    PlanAction.CREATE_NEW if create_source else PlanAction.CREATE_JOB
                )
                reason = (
                    "Recurso y Job nuevos a registrar en base de datos."
                    if create_source
                    else "Recurso existente. Ramificando nuevo Job para este chat."
                )

                item = PlanItem(
                    inspection=insp,
                    manifest=manifest,
                    action=action,
                    target_chat_id=target_chat_id,
                    total_parts=total_parts,
                    mutations=mutations,
                    parts_diff=parts_diff,
                    existing_source=existing_source,
                    existing_job=None,
                    reason=reason,
                )
                plan.items.append(item)

                planned_sources_md5.add(md5)
                planned_jobs.add(job_key)
                continue

            # El Job ya existe en BD. Análisis diferencial granular
            existing_payloads = {
                p.sequence_index: p for p in cast(list[Payload], existing_job.payloads)
            }
            existing_remotes = {
                r.payload.sequence_index: r
                for r in RemotePayload.select(RemotePayload, Payload)
                .join(Payload)
                .where(
                    (Payload.job == existing_job) & (RemotePayload.is_orphaned == False)
                )
            }

            parts_diff: List[PartDiff] = []
            has_conflict = False
            conflict_reason = ""

            for part in manifest.parts:
                seq = part.sequence
                db_payload = existing_payloads.get(seq)

                if db_payload is not None:  # noqa: SIM102
                    if (
                        db_payload.size != part.part_size
                        or db_payload.start_offset != part.start_offset
                    ):
                        has_conflict = True
                        conflict_reason = (
                            f"Discrepancia en parte {seq + 1}: "
                            f"BD espera {db_payload.size}B (offset {db_payload.start_offset}), "
                            f"snapshot indica {part.part_size}B (offset {part.start_offset})."
                        )
                        parts_diff.append(
                            PartDiff(
                                sequence=seq,
                                total_parts=total_parts,
                                message_id=part.message_id,
                                part_filename=part.part_filename,
                                part_size=part.part_size,
                                action=PartAction.CONFLICT,
                                detail=conflict_reason,
                            )
                        )
                        continue

                if seq in existing_remotes:
                    remote = existing_remotes[seq]
                    parts_diff.append(
                        PartDiff(
                            sequence=seq,
                            total_parts=total_parts,
                            message_id=part.message_id,
                            part_filename=part.part_filename,
                            part_size=part.part_size,
                            action=PartAction.ALREADY_EXISTS,
                            detail=f"Ya vinculada al mensaje {remote.message_id}",
                        )
                    )
                else:
                    parts_diff.append(
                        PartDiff(
                            sequence=seq,
                            total_parts=total_parts,
                            message_id=part.message_id,
                            part_filename=part.part_filename,
                            part_size=part.part_size,
                            action=PartAction.WILL_LINK,
                            detail="Falta en BD. Se vinculará como nuevo RemotePayload.",
                        )
                    )

            if has_conflict:
                plan.conflicts.append(
                    PlanItem(
                        inspection=insp,
                        manifest=manifest,
                        action=PlanAction.CONFLICT,
                        target_chat_id=target_chat_id,
                        total_parts=total_parts,
                        mutations=DbMutations(),
                        parts_diff=parts_diff,
                        existing_source=existing_source,
                        existing_job=existing_job,
                        reason=conflict_reason,
                    )
                )
                planned_jobs.add(job_key)
                continue

            missing_payloads = sum(
                1 for p in manifest.parts if p.sequence not in existing_payloads
            )
            missing_remotes = sum(
                1 for pd in parts_diff if pd.action == PartAction.WILL_LINK
            )

            # Criterio de sincronización total: partes remotas, payloads e inventario de cinta al día
            if missing_remotes == 0 and missing_payloads == 0 and missing_members == 0:
                plan.synced_count += 1
                planned_jobs.add(job_key)
                continue

            mutations = DbMutations(
                create_source=False,
                create_job=False,
                payloads_count=missing_payloads,
                remotes_count=missing_remotes,
                members_count=missing_members,
            )

            reason_parts = []
            if missing_remotes > 0:
                reason_parts.append(f"{missing_remotes}/{total_parts} piezas faltantes")
            if missing_members > 0:
                reason_parts.append(f"{missing_members} archivos de cinta a indexar")
            reason_detail = (
                ", ".join(reason_parts)
                if reason_parts
                else "sincronización de metadatos"
            )

            item = PlanItem(
                inspection=insp,
                manifest=manifest,
                action=PlanAction.MERGE_REMOTES,
                target_chat_id=target_chat_id,
                total_parts=total_parts,
                mutations=mutations,
                parts_diff=parts_diff,
                existing_source=existing_source,
                existing_job=existing_job,
                reason=f"Job existente incompleto. Se vincularán: {reason_detail}.",
            )
            plan.items.append(item)
            planned_jobs.add(job_key)

        return plan


class SnapshotImporter:
    """Ejecutor transaccional del plan de importación sobre la base de datos local."""

    @classmethod
    def apply_plan(
        cls,
        plan: ImportPlan,
        db: peewee.Database,
        owner: TelegramUser | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
        snapshot_message_id: int | None = None,
        client: "Client | None" = None,
    ) -> Tuple[int, List[Tuple[str, str]]]:
        actionable = plan.actionable_items
        succeeded = 0
        errors: List[Tuple[str, str]] = []

        for idx, item in enumerate(actionable, 1):
            try:
                with db_transaction(db):
                    cls._apply_item(
                        item,
                        owner,
                        snapshot_message_id=snapshot_message_id,
                        client=client,
                    )
                succeeded += 1
            except Exception as e:
                logger.exception(f"Error importando {item.inspection.path.name}")
                errors.append((item.inspection.path.name, str(e)))

            if on_progress:
                on_progress(idx, len(actionable), item.inspection.path.name)

        return succeeded, errors

    @classmethod
    def _apply_item(
        cls,
        item: PlanItem,
        owner: TelegramUser | None,
        snapshot_message_id: int | None = None,
        client: "Client | None" = None,
    ) -> None:
        """Aplica las mutaciones planificadas coordinando submétodos atómicos."""
        manifest = item.manifest

        resolved_snapshot_msg_id = snapshot_message_id or item.inspection.message_id

        chat = cls._ensure_destination_chat(item.target_chat_id)
        owner_obj = cls._ensure_owner_user(owner, manifest)
        source = cls._upsert_source(item.existing_source, manifest.source)
        job = cls._upsert_job(
            existing_job=item.existing_job,
            source=source,
            chat=chat,
            manifest=manifest,
            owner=owner_obj,
            snapshot_message_id=resolved_snapshot_msg_id,
        )

        payload_map = cls._sync_payloads(job, manifest.parts)

        will_link_sequences = {
            pd.sequence for pd in item.parts_diff if pd.action == PartAction.WILL_LINK
        }
        cls._link_remotes(
            payload_map=payload_map,
            parts=manifest.parts,
            will_link_sequences=will_link_sequences,
            owner=owner_obj,
            mime_type=manifest.source.mime_type,
        )

        # Ingesta masiva B-Tree de archivos si es contenedor .ttape
        if (
            manifest.source.type == SourceType.FOLDER
            and item.mutations.members_count > 0
        ):
            cls._bulk_ingest_tape_members(source, item.inspection.path)

        # Sincronizar el Recibo Agregado local si el archivo/carpeta original existe en disco
        try:
            source_file = Path(source.path_str).resolve()
            if source_file.exists():
                from totelegram.packaging.ledger import ReceiptLedger

                ReceiptLedger.record_upload(
                    file_path=source_file,
                    chat_id=chat.id,
                    snapshot_message_id=resolved_snapshot_msg_id,
                    chunk_size=job.chunk_size,
                    total_parts=len(manifest.parts),
                    md5=source.md5sum,
                    chat_title=chat.title,
                )
        except Exception as e:
            logger.debug(
                f"Aviso actualizando ReceiptLedger desde SnapshotImporter: {e}"
            )

        # AUTO-ENLACE / PUBLICACIÓN EN TELEGRAM
        if not job.remote_snapshot and client is not None:
            cls._resolve_or_publish_remote_snapshot(
                client=client,
                job=job,
                item=item,
                owner=owner_obj,
            )

    @classmethod
    def _resolve_or_publish_remote_snapshot(
        cls,
        client: "Client",
        job: Job,
        item: PlanItem,
        owner: TelegramUser,
    ) -> None:
        """
        Busca si el snapshot ya existe en Telegram vía #src_<hash>.
        Si existe, enlaza el RemoteSnapshot. Si no, lo sube y lo enlaza.
        """
        from pyrogram.types import Message

        from totelegram.models import RemoteSnapshot

        target_short_hash = job.source.short_hash
        query = f"#src_{target_short_hash}"

        # Comprobar si ya existe publicado en Telegram
        try:
            found_msgs = cast(
                List[Message],
                list(
                    client.search_messages(
                        chat_id=job.chat.id,
                        query=query,
                        limit=5,
                    )  # type: ignore
                ),
            )
            for m in found_msgs:
                if not m.document or not m.caption:
                    continue
                parsed = ManifestCaption.parse(m.caption)
                if parsed and parsed.source_short_hash == target_short_hash:
                    RemoteSnapshot.register(
                        job=job,
                        message_id=m.id,
                        chat=job.chat,
                        owner=owner,
                    )
                    logger.info(
                        f"Snapshot existente enlazado para Job {job.id} (Msg ID: {m.id})."
                    )
                    return
        except Exception as e:
            logger.warning(f"Aviso buscando snapshot existente en Telegram: {e}")

        # Si no existe y el archivo físico está presente en disco, lo publicamos
        snapshot_file = item.inspection.path
        if snapshot_file.is_file():
            try:
                sent_msg = SnapshotPublisher.publish(
                    client=client,
                    chat_id=job.chat.id,
                    snapshot_path=snapshot_file,
                    manifest=item.manifest,
                )
                RemoteSnapshot.register(
                    job=job,
                    message_id=sent_msg.id,
                    chat=job.chat,
                    owner=owner,
                )
                logger.info(
                    f"Snapshot publicado y enlazado para Job {job.id} (Msg ID: {sent_msg.id})."
                )
            except SnapshotPublicationError as e:
                logger.warning(
                    f"No se pudo auto-publicar el snapshot para Job {job.id}: {e}"
                )

    @staticmethod
    def _ensure_destination_chat(chat_id: int) -> TelegramChat:
        chat, _ = TelegramChat.get_or_create(
            id=chat_id,
            defaults={
                "title": f"Chat {chat_id}",
                "type": "channel",
                "is_public": False,
                "last_verified": datetime.now(timezone.utc),
            },
        )
        return chat

    @staticmethod
    def _ensure_owner_user(
        owner: TelegramUser | None, manifest: UploadManifest
    ) -> TelegramUser:
        if owner is not None:
            return owner

        owner_obj, _ = TelegramUser.get_or_create(
            id=manifest.owner_id,
            defaults={
                "first_name": manifest.owner_name or "Unknown",
                "username": None,
                "is_premium": False,
            },
        )
        return owner_obj

    @staticmethod
    def _upsert_source(
        existing_source: Source | None, source_meta: SourceMetadata
    ) -> Source:
        if existing_source is not None:
            return existing_source

        source = Source.get_or_none(Source.md5sum == source_meta.md5sum)
        if source is not None:
            return source

        return Source.create(
            path_str=source_meta.filename,
            md5sum=source_meta.md5sum,
            size=source_meta.size,
            mtime=source_meta.mtime,
            mimetype=source_meta.mime_type,
            type=source_meta.type,
            tape_catalog=source_meta.tape_catalog,
        )

    @staticmethod
    def _upsert_job(
        existing_job: Job | None,
        source: Source,
        chat: TelegramChat,
        manifest: UploadManifest,
        owner: TelegramUser,
        snapshot_message_id: int | None,
    ) -> Job:
        from totelegram.models import RemoteSnapshot

        if existing_job is not None:
            job = existing_job
        else:
            job = Job.get_for_source_in_chat(source, chat)

        if job is None:
            job = Job.create(
                source=source,
                chat=chat,
                chunk_size=manifest.chunk_size,
                last_verified_at=get_utc_now(),
            )

        # Si tenemos el ID del snapshot y no está registrado, lo vinculamos
        if snapshot_message_id and not job.remote_snapshot:
            RemoteSnapshot.register(
                job=job,
                message_id=snapshot_message_id,
                chat=chat,
                owner=owner,
            )

        return job

    @staticmethod
    def _sync_payloads(job: Job, parts: list[RemotePart]) -> dict[int, Payload]:
        payload_map = {p.sequence_index: p for p in cast(list[Payload], job.payloads)}

        for part in parts:
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

        return payload_map

    @staticmethod
    def _link_remotes(
        payload_map: dict[int, Payload],
        parts: list[RemotePart],
        will_link_sequences: set[int],
        owner: TelegramUser,
        mime_type: str,
    ) -> None:
        for part in parts:
            if part.sequence not in will_link_sequences:
                continue

            payload = payload_map[part.sequence]
            synthetic_meta = {
                "id": part.message_id,
                "chat": {"id": part.chat_id, "type": "channel"},
                "document": {
                    "file_name": part.part_filename,
                    "file_size": part.part_size,
                    "mime_type": mime_type,
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

    @staticmethod
    def _bulk_ingest_tape_members(source: Source, snapshot_path: Path) -> None:
        """Vuelca los archivos de la cinta TAR abriendo el SQLite de tartape en bloques."""
        if not (
            snapshot_path.suffix == ".ttape" or snapshot_path.name.endswith(".ttape")
        ):
            return

        with TapeContainer.open_catalog(snapshot_path) as cat:
            TapeMember.bulk_insert_from_catalog(source, cat)

    @classmethod
    def rehydrate_from_message(
        cls,
        client: "Client",
        message: "Message",
        db: peewee.Database,
        owner: TelegramUser | None = None,
    ) -> bool:
        """Descarga e hidrata un único mensaje de Telegram (JIT)."""
        if (
            not message
            or getattr(message, "empty", False) is True
            or not message.document
        ):
            return False

        with tempfile.TemporaryDirectory() as temp_dir:
            doc_name = message.document.file_name or f"snapshot_{message.id}"
            temp_file = Path(temp_dir) / doc_name

            downloaded = client.download_media(message, file_name=str(temp_file))
            if not downloaded or not temp_file.exists():
                return False

            inspection = SnapshotInspector.inspect_file(temp_file)
            if inspection.status != InspectionStatus.VALID or not inspection.manifest:
                return False

            plan = SnapshotImportPlanner.plan([inspection])
            if not plan.actionable_items:
                return True

            succeeded, _ = cls.apply_plan(
                plan=plan,
                db=db,
                owner=owner,
                snapshot_message_id=message.id,
            )
            return succeeded > 0

    @classmethod
    def rehydrate_from_messages(
        cls,
        client: "Client",
        messages: List["Message"],
        db: peewee.Database,
        owner: TelegramUser | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> RehydrationResult:
        """Descarga e importa snapshots desde una lista de mensajes de Telegram.

        Maneja un directorio temporal aislado, descarga los archivos, ejecuta
        la inspección, planifica y aplica las mutaciones a la base de datos local.
        """
        import tempfile

        if not messages:
            return RehydrationResult(succeeded_count=0, total_processed=0, errors=[])

        # Resolver el usuario actual de Telegram si no se proporcionó explícitamente
        if owner is None:
            try:
                me = client.get_me()
                if me:
                    owner = TelegramUser.get_or_create_from_tg(me)
            except Exception as e:
                logger.warning(
                    f"No se pudo resolver el usuario de Telegram durante la rehidratación: {e}"
                )

        total_processed = len(messages)
        inspections: List[SnapshotInspection] = []
        errors: List[Tuple[str, str]] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)

            for msg in messages:
                doc = getattr(msg, "document", None)
                doc_name = (
                    doc.file_name if doc and doc.file_name else f"tape_{msg.id}.ttape"
                )
                temp_file = temp_dir_path / f"{msg.id}_{doc_name}"

                try:
                    downloaded = client.download_media(msg, file_name=str(temp_file))
                    if not downloaded or not temp_file.exists():
                        errors.append(
                            (
                                doc_name,
                                f"Fallo al descargar el archivo desde Telegram (Msg ID: {msg.id}).",
                            )
                        )
                        continue

                    insp = SnapshotInspector.inspect_file(temp_file)
                    if insp.status != InspectionStatus.VALID or not insp.manifest:
                        err_msg = insp.error_detail or "Snapshot corrupto o inválido."
                        errors.append((doc_name, err_msg))
                        continue

                    inspections.append(insp)

                except Exception as e:
                    logger.warning(
                        f"Error procesando snapshot de Telegram (Msg ID {msg.id}): {e}"
                    )
                    errors.append((doc_name, str(e)))

            if not inspections:
                return RehydrationResult(
                    succeeded_count=0,
                    total_processed=total_processed,
                    errors=errors,
                )

            # Planificar mutaciones diferenciales
            plan = SnapshotImportPlanner.plan(inspections)

            # Aplicar en base de datos local
            succeeded, apply_errors = cls.apply_plan(
                plan=plan,
                db=db,
                owner=owner,
                on_progress=on_progress,
            )
            errors.extend(apply_errors)

            return RehydrationResult(
                succeeded_count=succeeded,
                total_processed=total_processed,
                errors=errors,
            )
