import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, cast

from pyrogram.types import Message

from totelegram.database import db_transaction
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.ledger import ReceiptLedger
from totelegram.packaging.publisher import SnapshotPublisher
from totelegram.packaging.schemas import (
    TTAPE_EXTENSION,
    RemotePart,
)
from totelegram.packaging.snapshot import SnapshotService
from totelegram.packaging.ttape import TapeContainer
from totelegram.schemas import SourceType

if TYPE_CHECKING:
    from pyrogram.client import Client

logger = logging.getLogger(__name__)


class ForwardError(Exception):
    """Excepción base para errores en el proceso de forward."""


@dataclass
class ForwardReport:
    """Resumen consolidado de una operación de replicación."""

    source_chat_id: int
    target_chat_id: int
    total_parts: int
    new_snapshot_message_id: int
    job: Job
    is_success: bool


class SnapshotForwarder:
    """Motor encargado de replicar un snapshot y sus partes hacia otro chat de Telegram

    utilizando copy_message a nivel de servidor (0 bytes descargados/subidos).
    """

    def __init__(self, client: "Client"):
        self.client = client

    def forward(
        self,
        snapshot_path: Path,
        target_chat: TelegramChat,
        owner: TelegramUser,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> ForwardReport:
        manifest = SnapshotService.load_snapshot(snapshot_path)
        source_chat_id = manifest.chat_id
        target_chat_id = target_chat.id

        if source_chat_id == target_chat_id:
            raise ForwardError(
                f"El chat de origen y destino son idénticos ({target_chat_id}). "
                "El archivo ya reside en este canal."
            )

        total_parts = len(manifest.parts)
        sorted_parts = sorted(manifest.parts, key=lambda p: p.sequence)

        # Validación JIT de piezas en el chat origen
        logger.info(
            f"Validando existencia de {total_parts} piezas en chat origen {source_chat_id}..."
        )
        source_msg_ids = [p.message_id for p in sorted_parts]
        try:
            source_messages = cast(
                List[Message],
                self.client.get_messages(source_chat_id, source_msg_ids),
            )
            for sm, part in zip(source_messages, sorted_parts):
                if not sm or sm.empty or not sm.document:
                    raise ForwardError(
                        f"La parte {part.sequence + 1} (Msg {part.message_id}) no existe o fue eliminada en el chat origen."
                    )
        except Exception as e:
            if isinstance(e, ForwardError):
                raise
            raise ForwardError(
                f"No se pudieron consultar los mensajes en el chat origen {source_chat_id}: {e}"
            ) from e

        # Replicación secuencial de partes en el destino
        copied_parts: List[tuple[RemotePart, Message]] = []
        try:
            for idx, part in enumerate(sorted_parts, 1):
                if on_progress:
                    on_progress(idx, total_parts, part.part_filename)

                new_msg = cast(
                    Message,
                    self.client.copy_message(
                        chat_id=target_chat_id,
                        from_chat_id=source_chat_id,
                        message_id=part.message_id,
                    ),
                )
                copied_parts.append((part, new_msg))
                logger.debug(
                    f"Parte {part.sequence + 1} copiada al destino: Msg {new_msg.id}"
                )
        except Exception as e:
            # Rollback: Ley de Basura Cero
            logger.warning(
                f"Error replicando partes. Ejecutando rollback en destino: {e}"
            )
            created_ids = [msg.id for _, msg in copied_parts]
            if created_ids:
                try:
                    self.client.delete_messages(target_chat_id, created_ids)
                except Exception as del_err:
                    logger.error(
                        f"Fallo durante rollback en chat {target_chat_id}: {del_err}"
                    )
            raise ForwardError(
                f"Fallo al copiar mensajes hacia el chat destino: {e}"
            ) from e

        # Construir el nuevo manifiesto actualizado para el chat destino
        new_remote_parts = [
            RemotePart(
                sequence=orig_part.sequence,
                message_id=new_msg.id,
                chat_id=target_chat_id,
                link=new_msg.link or "",
                part_filename=orig_part.part_filename,
                part_size=orig_part.part_size,
                part_md5sum=orig_part.part_md5sum,
                start_offset=orig_part.start_offset,
                end_offset=orig_part.end_offset,
            )
            for orig_part, new_msg in copied_parts
        ]

        new_manifest = manifest.model_copy(
            deep=True,
            update={
                "chat_id": target_chat_id,
                "owner_id": owner.id,
                "owner_name": owner.first_name,
                "parts": new_remote_parts,
            },
        )

        # Sellar y publicar el nuevo Snapshot en el chat destino
        is_folder = manifest.source.type == SourceType.FOLDER
        is_ttape = (
            snapshot_path.suffix == TTAPE_EXTENSION
            or snapshot_path.name.endswith(TTAPE_EXTENSION)
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            if is_folder and is_ttape:
                temp_catalog = tmp_path / "catalog.tartape"
                TapeContainer.extract_catalog(snapshot_path, temp_catalog)
                dest_snapshot_file = (
                    tmp_path / f"{manifest.source.filename}{TTAPE_EXTENSION}"
                )
                TapeContainer.build(
                    output_path=dest_snapshot_file,
                    manifest=new_manifest,
                    catalog_path=temp_catalog,
                    overwrite=True,
                )
            else:
                dest_snapshot_file = tmp_path / f"{manifest.source.filename}.json.xz"
                import lzma

                with lzma.open(dest_snapshot_file, "wt", encoding="utf-8") as f:
                    f.write(new_manifest.model_dump_json(indent=2))

            # Publicar el archivo snapshot sellado
            published_msg = SnapshotPublisher.publish(
                client=self.client,
                chat_id=target_chat_id,
                snapshot_path=dest_snapshot_file,
                manifest=new_manifest,
            )

        # Persistencia relacional en base de datos local SQLite
        with db_transaction(target_chat._meta.database):
            source = Source.get_or_none(Source.md5sum == manifest.source.md5sum)
            if not source:
                source = Source.create(
                    path_str=manifest.source.filename,
                    md5sum=manifest.source.md5sum,
                    size=manifest.source.size,
                    mtime=manifest.source.mtime,
                    mimetype=manifest.source.mime_type,
                    type=manifest.source.type,
                    tape_catalog=manifest.source.tape_catalog,
                )

            # Formalizar el Job en el destino
            job = Job.formalize_intent(
                source=source,
                chat=target_chat,
                chunk_size=manifest.chunk_size,
            )

            # Persistir Payloads y RemotePayloads
            for p_meta in new_remote_parts:
                payload, _ = Payload.get_or_create(
                    job=job,
                    sequence_index=p_meta.sequence,
                    defaults={
                        "filename": p_meta.part_filename,
                        "filename_short": p_meta.part_filename,
                        "size": p_meta.part_size,
                        "start_offset": p_meta.start_offset,
                        "end_offset": p_meta.end_offset,
                        "md5sum": p_meta.part_md5sum,
                    },
                )
                RemotePayload.create(
                    payload=payload,
                    message_id=p_meta.message_id,
                    chat=target_chat,
                    owner=owner,
                    json_metadata={"id": p_meta.message_id, "link": p_meta.link},
                )

            # Sellar RemoteSnapshot
            job.set_snapshot_published(published_msg.id, owner)

        # Actualizar el Recibo Agregado si el archivo original existe en disco
        try:
            orig_file = snapshot_path.parent / manifest.source.filename
            if orig_file.exists():
                ReceiptLedger.record_upload(
                    file_path=orig_file,
                    chat_id=target_chat_id,
                    snapshot_message_id=published_msg.id,
                    chunk_size=manifest.chunk_size,
                    total_parts=total_parts,
                    md5=manifest.source.md5sum,
                    chat_title=target_chat.title,
                )
        except Exception as e:
            logger.debug(f"Aviso actualizando ReceiptLedger en forward: {e}")

        return ForwardReport(
            source_chat_id=source_chat_id,
            target_chat_id=target_chat_id,
            total_parts=total_parts,
            new_snapshot_message_id=published_msg.id,
            job=job,
            is_success=True,
        )
