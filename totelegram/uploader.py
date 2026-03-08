import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Tuple, cast

import tartape

from totelegram.cli.console import UI
from totelegram.models import Job, Payload, RemotePayload
from totelegram.packaging import Chunker
from totelegram.schemas import SourceType
from totelegram.stream import FileVolume
from totelegram.types import AvailabilityReport
from totelegram.utils import ThrottledFile

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Message, User

    from totelegram.cli.upload import UploadContext


logger = logging.getLogger(__name__)


class UploadProgress:
    def __init__(self):
        self.last_percentage = -1

    def __call__(self, current: int, total: int):
        percentage = int(current * 100 / total)

        # reporta cada 1%
        if percentage % 1 == 0 and self.last_percentage != percentage:
            self.last_percentage = percentage

            UI.info(f"Subiendo {current} de {total} bytes {percentage}%", end="\r")


class UploadService:
    # TODO: Luego de consolidar la logica. Hay que sacar los UI de aqui.
    def __init__(
        self,
        u_ctx: "UploadContext",
    ):
        self.client = u_ctx.client
        self.limit_rate_kbps = u_ctx.settings.upload_limit_rate_kbps
        self.max_filename_len = u_ctx.settings.max_filename_length

        self.settings = u_ctx.settings
        self.client = u_ctx.client
        self.db = u_ctx.db
        self.u_ctx = u_ctx
        self.owner = u_ctx.owner
        self.tg_chat = u_ctx.tg_chat

    def execute_physical_upload(self, job: Job, path: Path):
        payloads = Chunker.get_or_create(self.db, job)
        md5sum = job.source.md5sum
        for payload in payloads:
            if payload.has_remote:
                continue

            message, part_md5 = self._upload_payload(
                job.source.type, md5sum, path, payload
            )

            with self.u_ctx.db.atomic():
                payload.md5sum = part_md5
                payload.save(only=[Payload.md5sum])
                RemotePayload.register_upload(payload, message, self.owner)

        job.set_uploaded()

    def execute_smart_forward(self, job: Job, report: AvailabilityReport):
        mirrros = {r.payload.sequence_index: r for r in report.remotes}
        UI.info(f"Reenviando {len(mirrros)} partes...")

        job_adopted = job.adopt_job(report.remotes[0].payload.job)
        md5sum = job_adopted.source.md5sum
        for payload_adopted in Chunker.get_or_create(self.db, job_adopted):
            if payload_adopted.has_remote:
                continue

            remote_mirror = mirrros[payload_adopted.sequence_index]
            message = self._smart_forward_strategy(
                md5sum, payload_adopted, remote_mirror
            )
            RemotePayload.register_upload(payload_adopted, message, self.owner)
            time.sleep(1)

        job_adopted.set_uploaded()

    def resolve_naming_payload(
        self, source_md5sum: str, payload: "Payload"
    ) -> Tuple[str, str]:

        filename = payload.filename
        caption = ""

        if len(payload.filename) >= self.max_filename_len:
            suffix = Path(filename).suffix
            filename = f"{source_md5sum}{suffix}"
            caption = payload.filename

        return filename, caption

    def _upload_payload(
        self, source_type: SourceType, md5sum: str, path: Path, payload: Payload
    ):

        if source_type == SourceType.FOLDER:
            tape= tartape.Tape(path)
            volumen= tape.get_volume(payload.filename, payload.sequence_index,payload.start_offset, payload.end_offset)
        else:
            volumen = FileVolume(path, payload.start_offset, payload.end_offset, payload.filename)

        limit_bytes = self.u_ctx.settings.upload_limit_rate_kbps * 1024
        with volumen:
            with ThrottledFile(volumen, limit_bytes) as doc_stream:
                filename, caption = self.resolve_naming_payload(md5sum, payload)

                tg_message = cast(
                    "Message",
                    self.client.send_document(
                        chat_id=self.tg_chat.id,
                        document=doc_stream,  # type: ignore
                        file_name=filename,
                        caption=caption,
                        progress=UploadProgress(),
                    ),
                )

                return tg_message, volumen.md5sum

    def _smart_forward_strategy(
        self,
        md5sum: str,
        payload_adopted: Payload,
        remote_mirror: RemotePayload,
    ) -> "Message":
        filename, caption = self.resolve_naming_payload(md5sum, payload_adopted)
        return cast(
            "Message",
            self.client.send_document(
                chat_id=self.tg_chat.id,
                document=remote_mirror.message.document.file_id,
                file_name=filename,
                caption=caption,
            ),
        )
