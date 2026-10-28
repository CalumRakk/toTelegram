import json
import logging
import lzma
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import peewee
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

LEDGER_FILENAME = ".totelegram.json"
LEDGER_SCHEMA_VERSION = 1


def is_mtime_matching(cached_mtime: float, current_mtime: float) -> bool:
    """
    Comprueba si dos mtimes coinciden considerando precisiones de sistemas de archivos.
    Tolera hasta 2 segundos en sistemas sin subsegundos (FAT32/ZIP) y 1ms en modernos.
    """
    diff = abs(cached_mtime - current_mtime)
    if diff < 0.001:
        return True

    is_cached_integer = (cached_mtime % 1.0) == 0.0
    is_current_integer = (current_mtime % 1.0) == 0.0
    return bool((is_cached_integer or is_current_integer) and diff <= 2.0)


class BackupDestinationRecord(BaseModel):
    """Metadatos de la subida de un archivo hacia un destino específico en Telegram."""

    chat_id: int
    chat_title: str | None = None
    snapshot_message_id: int
    chunk_size: int
    total_parts: int
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FileReceiptEntry(BaseModel):
    """Registro de estado de un archivo local y sus destinos en Telegram."""

    md5: str
    size: int
    mtime: float
    destinations: Dict[str, BackupDestinationRecord] = Field(default_factory=dict)

    def is_fresh(self, current_size: int, current_mtime: float) -> bool:
        """Determina si el archivo en disco no ha cambiado desde que se registró."""
        return self.size == current_size and is_mtime_matching(
            self.mtime, current_mtime
        )


class DirectoryReceipt(BaseModel):
    """Manifiesto unificado de recibos para todos los archivos dentro de un directorio."""

    version: int = LEDGER_SCHEMA_VERSION
    entries: Dict[str, FileReceiptEntry] = Field(default_factory=dict)


class ReceiptLedger:
    """
    Gestor atómico del Recibo Agregado por directorio (.totelegram.json).
    Permite registrar y consultar disponibilidad multi-canal sin contaminar el sistema de archivos.
    """

    @classmethod
    def get_ledger_path(cls, dir_path: Path | str) -> Path:
        """Obtiene la ruta esperada del archivo .totelegram.json en el directorio."""
        return Path(dir_path).resolve() / LEDGER_FILENAME

    @classmethod
    def load(cls, dir_path: Path | str) -> DirectoryReceipt:
        """
        Carga y deserializa el recibo del directorio.
        Si no existe o está corrupto, devuelve un DirectoryReceipt vacío y seguro.
        """
        target = cls.get_ledger_path(dir_path)
        if not target.is_file():
            return DirectoryReceipt()

        try:
            raw_text = target.read_text(encoding="utf-8")
            return DirectoryReceipt.model_validate_json(raw_text)
        except Exception as e:
            logger.warning(
                f"Aviso: El archivo de recibo '{target}' contiene errores o está corrupto ({e}). "
                "Iniciando recibo nuevo en memoria."
            )
            return DirectoryReceipt()

    @classmethod
    def save(cls, dir_path: Path | str, receipt: DirectoryReceipt) -> Path:
        """
        Escribe de forma atómica el recibo en disco para evitar archivos a medio escribir.
        Usa un archivo temporal con sufijo único y reemplazo atómico a nivel de OS.
        """
        target_dir = Path(dir_path).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        final_path = target_dir / LEDGER_FILENAME

        # Escritura atómica mediante tempfile en el mismo directorio (mismo filesystem)
        temp_filename = f".{LEDGER_FILENAME}.tmp_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        temp_path = target_dir / temp_filename

        try:
            json_data = receipt.model_dump_json(indent=2)
            temp_path.write_text(json_data, encoding="utf-8")
            temp_path.replace(final_path)
            return final_path
        except Exception as e:
            temp_path.unlink(missing_ok=True)
            logger.error(f"Error al escribir el recibo atómico en '{final_path}': {e}")
            raise

    @classmethod
    def record_upload(
        cls,
        file_path: Path | str,
        chat_id: int,
        snapshot_message_id: int,
        chunk_size: int,
        total_parts: int,
        md5: str,
        chat_title: str | None = None,
    ) -> DirectoryReceipt:
        """
        Registra o actualiza de forma idempotente el respaldo de un archivo para un canal.
        Lee el recibo existente, añade el destino y guarda atómicamente.
        """
        path = Path(file_path).resolve()
        dir_path = path.parent
        filename = path.name

        stat = path.stat()
        current_size = stat.st_size
        current_mtime = stat.st_mtime

        receipt = cls.load(dir_path)

        entry = receipt.entries.get(filename)
        if entry is None:
            entry = FileReceiptEntry(
                md5=md5,
                size=current_size,
                mtime=current_mtime,
                destinations={},
            )
            receipt.entries[filename] = entry
        else:
            entry.md5 = md5
            entry.size = current_size
            entry.mtime = current_mtime

        dest_key = str(chat_id)
        entry.destinations[dest_key] = BackupDestinationRecord(
            chat_id=chat_id,
            chat_title=chat_title,
            snapshot_message_id=snapshot_message_id,
            chunk_size=chunk_size,
            total_parts=total_parts,
            uploaded_at=datetime.now(timezone.utc),
        )

        cls.save(dir_path, receipt)
        logger.debug(
            f"Recibo actualizado: '{filename}' respaldado en chat {chat_id} (Msg: {snapshot_message_id})"
        )
        return receipt

    @classmethod
    def is_backed_up(
        cls,
        file_path: Path | str,
        chat_id: int | str | None = None,
        receipt: DirectoryReceipt | None = None,
    ) -> bool:
        path = Path(file_path).resolve()
        if not path.exists():
            return False

        active_receipt = receipt if receipt is not None else cls.load(path.parent)
        entry = active_receipt.entries.get(path.name)

        # Si no existe en el ledger actual, intentamos asimilar un snapshot legado
        if not entry and cls.assimilate_legacy_snapshot(path):
            active_receipt = cls.load(path.parent)
            entry = active_receipt.entries.get(path.name)

        # Verificación en el Ledger moderno (.totelegram.json)
        if entry:
            stat = path.stat()
            if entry.is_fresh(stat.st_size, stat.st_mtime):
                if chat_id is None:
                    return len(entry.destinations) > 0
                return str(chat_id) in entry.destinations
            return False

        # Fallback de compatibilidad hacia atrás si no se pudo asimilar
        from totelegram.utils import has_snapshot

        return has_snapshot(path)

    @classmethod
    def assimilate_legacy_snapshot(cls, file_path: Path | str) -> bool:
        """
        Detecta y asimila snapshots heredados (.json.xz o .ttape) para file_path.
        Aplica cascada heurística para soportar tanto v6.0 como esquemas arqueológicos.
        """
        path = Path(file_path).resolve()
        dir_path = path.parent

        # Buscar posibles sidecars en disco
        targets = [
            dir_path / f"{path.name}.json.xz",
            dir_path / f"{path.stem}.json.xz",
            dir_path / f"{path.name}.ttape",
            dir_path / f"{path.stem}.ttape",
        ]
        sidecar = next((t for t in targets if t.is_file()), None)
        if not sidecar:
            return False

        try:
            # Caso 1: Contenedor .ttape
            if sidecar.suffix == ".ttape" or sidecar.name.endswith(".ttape"):
                from totelegram.packaging.ttape import TapeContainer

                manifest = TapeContainer.read_manifest(sidecar)
                cls.record_upload(
                    file_path=path,
                    chat_id=manifest.chat_id,
                    snapshot_message_id=(
                        manifest.parts[0].message_id if manifest.parts else 0
                    ),
                    chunk_size=manifest.chunk_size,
                    total_parts=len(manifest.parts),
                    md5=manifest.source.md5sum,
                )
                return True

            # Caso 2: Snapshot comprimido .json.xz
            with lzma.open(sidecar, "rt", encoding="utf-8") as f:
                raw_data = json.load(f)

            if not isinstance(raw_data, dict):
                return False

            # Cascada para chat_id
            chat_id = raw_data.get("chat_id") or raw_data.get("config", {}).get(
                "chat_id"
            )
            if not chat_id and raw_data.get("parts"):
                chat_id = raw_data["parts"][0].get("chat_id")

            if not chat_id:
                return False

            # Cascada para parts
            raw_parts = raw_data.get("parts", [])
            total_parts = len(raw_parts) or 1

            # Cascada heurística para chunk_size (moderno vs arqueológico)
            chunk_size = (
                raw_data.get("chunk_size")
                or raw_data.get("config", {}).get("tg_max_size")
                or (
                    raw_parts[0].get("size") or raw_parts[0].get("part_size")
                    if len(raw_parts) > 1
                    else None
                )
                or raw_data.get("source", {}).get("size")
                or (path.stat().st_size if path.exists() else 2000 * 1024 * 1024)
            )

            # Cascada para snapshot_message_id
            msg_id = (
                raw_data.get("snapshot_message_id")
                or raw_data.get("message_id")
                or (raw_parts[0].get("message_id") if raw_parts else 0)
            )

            # Cascada para MD5
            md5sum = (
                raw_data.get("source", {}).get("md5sum")
                or raw_data.get("md5")
                or (raw_parts[0].get("part_md5sum") if raw_parts else "")
            )

            cls.record_upload(
                file_path=path,
                chat_id=int(chat_id),
                snapshot_message_id=int(msg_id),
                chunk_size=int(chunk_size),
                total_parts=total_parts,
                md5=str(md5sum),
            )
            logger.info(
                f"Snapshot heredado '{sidecar.name}' asimilado exitosamente en {LEDGER_FILENAME}."
            )
            return True

        except Exception as e:
            logger.debug(
                f"No se pudo asimilar el snapshot heredado '{sidecar.name}': {e}"
            )
            return False

    @classmethod
    def heal_directory_from_db(
        cls, dir_path: Path | str, db: peewee.Database | None = None
    ) -> int:
        """
        Auto-sana y reconstruye el .totelegram.json de un directorio a partir de SQLite.
        Inspecciona Jobs completados con RemoteSnapshot activo para archivos dentro de `dir_path`.
        """
        from totelegram.models import Job, RemoteSnapshot, Source, TelegramChat

        target_dir = Path(dir_path).resolve()
        if not target_dir.is_dir():
            return 0

        query = (
            Job.select(Job, Source, RemoteSnapshot, TelegramChat)
            .join(Source)
            .switch(Job)
            .join(RemoteSnapshot)
            .switch(Job)
            .join(TelegramChat)
            .where(RemoteSnapshot.is_orphaned == False)
        )

        healed_count = 0

        for job in query:
            source_file = Path(job.source.path_str).resolve()
            if source_file.parent != target_dir:
                continue

            if not source_file.exists():
                continue

            stat = source_file.stat()
            # Validar frescura con el Source en base de datos
            if stat.st_size == job.source.size and is_mtime_matching(
                job.source.mtime, stat.st_mtime
            ):
                cls.record_upload(
                    file_path=source_file,
                    chat_id=job.chat.id,
                    snapshot_message_id=job.remote_snapshot.message_id,
                    chunk_size=job.chunk_size,
                    total_parts=job.payloads.count() or 1,
                    md5=job.source.md5sum,
                    chat_title=job.chat.title,
                )
                healed_count += 1

        logger.info(
            f"Auto-sanación completada: {healed_count} entradas restauradas en {target_dir / LEDGER_FILENAME}."
        )
        return healed_count

    @classmethod
    def get_destination(
        cls, file_path: Path | str, chat_id: int | str
    ) -> BackupDestinationRecord | None:
        """Obtiene el registro de respaldo de un archivo para un chat específico."""
        path = Path(file_path).resolve()
        receipt = cls.load(path.parent)
        entry = receipt.entries.get(path.name)
        if not entry:
            return None
        return entry.destinations.get(str(chat_id))

    @classmethod
    def remove_destination(cls, file_path: Path | str, chat_id: int | str) -> bool:
        """Elimina un destino específico del recibo de un archivo."""
        path = Path(file_path).resolve()
        dir_path = path.parent
        receipt = cls.load(dir_path)

        entry = receipt.entries.get(path.name)
        if not entry:
            return False

        dest_key = str(chat_id)
        if dest_key in entry.destinations:
            del entry.destinations[dest_key]
            cls.save(dir_path, receipt)
            return True
        return False
