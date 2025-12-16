import logging
import lzma
from pathlib import Path

from totelegram.models import Job, Payload, RemotePayload
from totelegram.schemas import (
    MANIFEST_VERSION,
    RemotePart,
    SourceMetadata,
    UploadManifest,
)

from .parse import parse_message_json_data

logger = logging.getLogger(__name__)


class SnapshotService:
    @staticmethod
    def generate_snapshot(job: Job) -> UploadManifest:
        """
        Genera el manifiesto (snapshot) moderno basado en el Job.
        """
        source = job.source
        output_path = Path(source.path_str).with_suffix(".json.xz")

        logger.info(
            f"Generando manifiesto {MANIFEST_VERSION} para Job {job.id} -> {output_path.name}"
        )

        source_meta = SourceMetadata(
            filename=Path(source.path_str).name,
            size=source.size,
            md5sum=source.md5sum,
            mime_type=source.mimetype,
        )

        parts: list[RemotePart] = []

        payloads = job.payloads.order_by(Payload.sequence_index)  # type: ignore
        for payload in payloads:
            remote: RemotePayload = RemotePayload.get_or_none(
                RemotePayload.payload == payload
            )
            if not remote:
                logger.warning(
                    f"El payload {payload.sequence_index} no tiene RemotePayload. El manifiesto estará incompleto."
                )
                raise ValueError(
                    f"Falta RemotePayload para Payload {payload.id} del Job {job.id}"
                )

            message = parse_message_json_data(remote.json_metadata)
            part = RemotePart(
                sequence=payload.sequence_index,
                message_id=remote.message_id,
                chat_id=remote.chat_id,
                link=message.link,
                part_filename=Path(payload.temp_path).name,
                part_size=payload.size,
            )
            parts.append(part)

        manifest = UploadManifest(
            strategy=job.strategy, source=source_meta, parts=parts
        )

        with lzma.open(output_path, "wt", encoding="utf-8") as f:
            f.write(manifest.model_dump_json(indent=2))

        return manifest
