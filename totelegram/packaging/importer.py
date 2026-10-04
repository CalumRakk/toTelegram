import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple, cast

import peewee

from totelegram import __VERSION__
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
from totelegram.packaging.schemas import MANIFEST_VERSION, UploadManifest
from totelegram.packaging.upgrader import SnapshotUpgrader, load_raw_snapshot
from totelegram.schemas import JobStatus, SourceType, StrategyConfig

logger = logging.getLogger(__name__)

# ESTADOS ATÓMICOS DE INSPECCIÓN Y PIEZAS


class InspectionStatus(str, enum.Enum):
    VALID = "valid"  # UploadManifest v5.0 íntegro
    NEEDS_UPGRADE = "needs_upgrade"  # Versión antigua (requiere comando upgrade)
    CORRUPTED = "corrupted"  # JSON/YAML roto o schema inválido


class PartAction(str, enum.Enum):
    """Acción atómica para cada fragmento físico en Telegram."""

    ALREADY_EXISTS = "already_exists"  # Ya registrado en BD con este message_id
    WILL_LINK = "will_link"  # Nueva parte remota a registrar
    CONFLICT = "conflict"  # Discrepancia física (offsets o hashes)


class PlanAction(str, enum.Enum):
    """Acción macro sobre el recurso en la Base de Datos."""

    CREATE_NEW = "CREATE_NEW"  # Recurso y Job completamente nuevos
    CREATE_JOB = "CREATE_JOB"  # Source existente, nuevo Job en este chat
    MERGE_REMOTES = "MERGE_REMOTES"  # Job existente, se completan piezas faltantes
    ALREADY_EXISTS = "ALREADY_EXISTS"  # Todo al día (se vuelve estadística)
    CONFLICT = "CONFLICT"  # Discrepancia insalvable en el particionado


# DTOs DEL DIFERENCIAL Y MUTACIONES


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
    members_count: int = 0  # Cantidad de archivos a indexar si es carpeta TAR

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
            ops.append("+Source")
        if self.create_job:
            ops.append("+Job")
        if self.payloads_count > 0:
            ops.append(f"+{self.payloads_count}P")
        if self.remotes_count > 0:
            ops.append(f"+{self.remotes_count}Remotes")
        if self.members_count > 0:
            ops.append(f"+{self.members_count}Files")
        return "  ".join(ops) if ops else "[dim]Sin cambios[/]"


@dataclass
class SnapshotInspection:
    path: Path
    status: InspectionStatus
    manifest: Optional[UploadManifest] = None
    detected_version: Optional[str] = None
    error_detail: Optional[str] = None


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
    existing_source: Optional[Source] = None
    existing_job: Optional[Job] = None
    reason: str = ""

    @property
    def is_partial(self) -> bool:
        """Indica si el recurso tiene una mezcla de partes existentes y nuevas."""
        has_existing = any(
            p.action == PartAction.ALREADY_EXISTS for p in self.parts_diff
        )
        has_new = any(p.action == PartAction.WILL_LINK for p in self.parts_diff)
        return has_existing and has_new

    @property
    def missing_parts_count(self) -> int:
        return self.mutations.remotes_count


@dataclass
class ImportPlan:
    """El contenedor maestro que organiza el escaneo en sus cubetas limpias."""

    # Silenciosos: completamente al día (solo suman a la estadística)
    synced_count: int = 0

    # Accionables: los que tienen mutaciones reales y van a la tabla
    items: List[PlanItem] = field(default_factory=list)

    # Conflictos: discrepancias estructurales que bloquean la importación
    conflicts: List[PlanItem] = field(default_factory=list)

    # Atención requerida externa
    needs_upgrade_items: List[SnapshotInspection] = field(default_factory=list)
    corrupted_items: List[SnapshotInspection] = field(default_factory=list)

    @property
    def total_scanned(self) -> int:
        return (
            self.synced_count
            + len(self.items)
            + len(self.conflicts)
            + len(self.needs_upgrade_items)
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
                error_detail="El contenido no es un diccionario JSON/YAML válido.",
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
        """Encuentra recursivamente todos los snapshots con comparación case-insensitive."""
        valid_exts = (".json", ".json.xz", ".yaml", ".yml", ".yaml.xz", ".yml.xz")
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
        return sorted(list(set(candidates)))


class SnapshotImportPlanner:
    @classmethod
    def plan(cls, inspections: List[SnapshotInspection]) -> ImportPlan:
        """
        Evalúa de forma determinista el diferencial entre los snapshots inspeccionados
        y la base de datos, calculando las mutaciones exactas a nivel de pieza (PartDiff).
        """
        plan = ImportPlan()

        # Índices en memoria para evitar colisiones dentro del mismo lote
        planned_sources_md5: set[str] = set()
        planned_jobs: set[Tuple[str, int]] = set()

        for insp in inspections:
            # Filtro temprano: No accionables externos
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
            md5 = manifest.source.md5sum
            job_key = (md5, target_chat_id)

            # Descarte de duplicados exactos dentro del mismo lote
            if job_key in planned_jobs:
                plan.synced_count += 1
                continue

            # Inspeccionar existencia de Source y Job en BD
            existing_source = Source.get_or_none(Source.md5sum == md5)
            source_exists = existing_source is not None or md5 in planned_sources_md5

            existing_job: Optional[Job] = None
            if existing_source:
                existing_job = Job.get_or_none(
                    (Job.source == existing_source)
                    & (Job.chat == target_chat_id)
                    & (Job.deleted_at == 0)
                )

            # CASO A: Recurso completamente nuevo o Job no existente en este chat
            if existing_job is None:
                create_source = not source_exists
                create_job = True

                # Todas las partes son nuevas en este chat
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

                # Conteo de archivos internos si es carpeta TAR
                members_count = 0
                if (
                    manifest.source.type == SourceType.FOLDER
                    and manifest.source.inventory
                ):
                    members_count = len(manifest.source.inventory)

                mutations = DbMutations(
                    create_source=create_source,
                    create_job=create_job,
                    payloads_count=total_parts,
                    remotes_count=total_parts,
                    members_count=members_count,
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

            # CASO B: El Job ya existe en BD. Análisis diferencial granular pieza por pieza
            existing_payloads = {
                p.sequence_index: p for p in cast(list[Payload], existing_job.payloads)
            }
            existing_remotes = {
                r.payload.sequence_index: r
                for r in RemotePayload.select(RemotePayload, Payload)
                .join(Payload)
                .where(
                    (Payload.job == existing_job) & (RemotePayload.is_orphaned == False)  # noqa: E712
                )
            }

            parts_diff: List[PartDiff] = []
            has_conflict = False
            conflict_reason = ""

            for part in manifest.parts:
                seq = part.sequence
                db_payload = existing_payloads.get(seq)

                # Auditoría física contra Payload en BD (inconsistencia de particionado)
                if db_payload is not None:
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

                # Auditoría de RemotePayload (¿Mensaje ya vinculado?)
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

            # Si se detectó discrepancia física, se aisla como conflicto bloqueante
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

            # Calcular mutaciones requeridas
            missing_payloads = sum(
                1 for p in manifest.parts if p.sequence not in existing_payloads
            )
            missing_remotes = sum(
                1 for pd in parts_diff if pd.action == PartAction.WILL_LINK
            )

            # Si ya tiene el 100% de las piezas vinculadas: SE VUELVE ESTADÍSTICA
            if missing_remotes == 0 and missing_payloads == 0:
                plan.synced_count += 1
                planned_jobs.add(job_key)
                continue

            # Si le faltan piezas: se marca como MERGE_REMOTES
            mutations = DbMutations(
                create_source=False,
                create_job=False,
                payloads_count=missing_payloads,
                remotes_count=missing_remotes,
                members_count=0,
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
                reason=f"Job existente incompleto. Se vincularán {missing_remotes}/{total_parts} piezas faltantes.",
            )
            plan.items.append(item)
            planned_jobs.add(job_key)

        return plan


class SnapshotImporter:
    @classmethod
    def apply_plan(
        cls,
        plan: ImportPlan,
        db: peewee.Database,
        owner: Optional[TelegramUser] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> Tuple[int, List[Tuple[str, str]]]:
        """
        Ejecuta las inserciones atómicas en la DB a partir de los ítems accionables del plan.
        Retorna (exitosos, lista_de_errores[(nombre_archivo, razon)]).
        """
        actionable = plan.actionable_items
        succeeded = 0
        errors: List[Tuple[str, str]] = []

        for idx, item in enumerate(actionable, 1):
            try:
                with db_transaction(db):
                    cls._apply_item(item, owner)
                succeeded += 1
            except Exception as e:
                logger.exception(f"Error importando {item.inspection.path.name}: {e}")
                errors.append((item.inspection.path.name, str(e)))

            if on_progress:
                on_progress(idx, len(actionable), item.inspection.path.name)

        return succeeded, errors

    @classmethod
    def _apply_item(cls, item: PlanItem, owner: Optional[TelegramUser]):
        manifest = item.manifest

        # Resolver Chat
        chat, _ = TelegramChat.get_or_create(
            id=item.target_chat_id,
            defaults={
                "title": f"Chat {item.target_chat_id}",
                "type": "channel",
                "is_public": False,
                "last_verified": datetime.now(timezone.utc),
            },
        )

        # Resolver Owner (usuario actual o el registrado en el snapshot)
        owner_obj = owner
        if owner_obj is None:
            owner_obj, _ = TelegramUser.get_or_create(
                id=manifest.owner_id,
                defaults={
                    "first_name": manifest.owner_name or "Unknown",
                    "username": None,
                    "is_premium": False,
                },
            )

        # Resolver o Crear Source
        source = item.existing_source
        if source is None:
            source = Source.get_or_none(Source.md5sum == manifest.source.md5sum)

        if source is None:
            source = Source.create(
                path_str=manifest.source.filename,
                md5sum=manifest.source.md5sum,
                size=manifest.source.size,
                mtime=manifest.source.mtime,
                mimetype=manifest.source.mime_type,
                type=manifest.source.type,
                tape_catalog=manifest.source.tape_catalog,
            )

        # Resolver o Crear Job
        job = item.existing_job
        if job is None:
            job = Job.get_or_none(
                (Job.source == source) & (Job.chat == chat) & (Job.deleted_at == 0)
            )

        if job is None:
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

        # Mapear Payloads existentes y asegurar que existan todos
        payload_map = {p.sequence_index: p for p in cast(list[Payload], job.payloads)}

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

        # Vincular quirúrgicamente SOLO las partes planificadas como WILL_LINK
        will_link_sequences = {
            pd.sequence for pd in item.parts_diff if pd.action == PartAction.WILL_LINK
        }

        for part in manifest.parts:
            if part.sequence not in will_link_sequences:
                continue

            payload = payload_map[part.sequence]
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
                owner=owner_obj,
                json_metadata=synthetic_meta,
                last_verified_at=datetime.now(timezone.utc),
                is_orphaned=False,
            )

        # Indexar miembros y GPS si es una carpeta TAR y aún no estaban
        if (
            manifest.source.type == SourceType.FOLDER
            and manifest.source.inventory
            and item.mutations.members_count > 0
        ):
            member_inserts = [
                {
                    "source": source,
                    "relative_path": m.relative_path,
                    "size": m.size,
                    "md5sum": m.md5sum,
                }
                for m in manifest.source.inventory
            ]
            for batch in batched(member_inserts, 100):
                TapeMember.insert_many(batch).on_conflict_ignore().execute()

            # Mapear relative_path -> id
            paths = [m.relative_path for m in manifest.source.inventory]
            db_members = TapeMember.select(
                TapeMember.id, TapeMember.relative_path
            ).where((TapeMember.source == source) & (TapeMember.relative_path << paths))  # type: ignore
            path_to_id = {m.relative_path: m.id for m in db_members}

            gps_inserts = []
            for m in manifest.source.inventory:
                member_id = path_to_id.get(m.relative_path)
                if not member_id:
                    continue

                for frag in m.fragments:
                    payload_obj = payload_map.get(frag.vol_idx)
                    if not payload_obj:
                        continue

                    gps_inserts.append(
                        {
                            "member": member_id,
                            "payload": payload_obj,
                            "state": "COMPLETED",
                            "offset_in_volume": frag.offset_in_vol,
                            "bytes_in_volume": frag.bytes_in_volume,
                        }
                    )

            for batch in batched(gps_inserts, 200):
                TapeMemberGPS.insert_many(batch).execute()

        # Asegurar que el estado del Job quede como UPLOADED si ya no tiene piezas pendientes
        if Payload.total_pending_for_job(job) == 0:
            if job.status != JobStatus.UPLOADED:
                job.status = JobStatus.UPLOADED
                job.save(only=[Job.status, Job.updated_at])
