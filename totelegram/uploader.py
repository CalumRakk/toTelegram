import logging
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, Tuple, cast

import tartape

from totelegram.cli.ui import UI
from totelegram.models import Job, Payload, RemotePayload
from totelegram.packaging import Chunker
from totelegram.schemas import SourceType
from totelegram.stream import FileVolume
from totelegram.types import AvailabilityReport, UploadContext
from totelegram.utils import ThrottledFile

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Message, User


logger = logging.getLogger(__name__)


class UploadProgress:
    def __init__(self, filename: str):
        self.filename = filename
        self.last_percentage = -1

    def __call__(self, current: int, total: int):
        percentage = int(current * 100 / total)

        if percentage != self.last_percentage:
            self.last_percentage = percentage

            curr_mb = current / (1024 * 1024)
            tot_mb = total / (1024 * 1024)

            msg = f"  ↑ {self.filename} ({curr_mb:.1f}/{tot_mb:.1f} MB) {percentage}%"
            print(f"\r{msg}\033[K", end="", flush=True)

    def finish(self):
        """Limpia la línea de progreso para dejar espacio al siguiente mensaje."""
        print("\r\033[K", end="", flush=True)


class UploadService:
    # TODO: Luego de consolidar la logica. Hay que sacar los UI de aqui.
    def __init__(
        self,
        u_ctx: UploadContext,
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

    def _smart_pause(self):
        """Calcula y ejecuta una pausa aleatoria basada en la configuración."""
        r = self.settings.upload_pause_range

        minutes = random.randint(min(r), max(r))

        if minutes > 0:
            UI.sleep_progress(minutes * 60)

    def execute_physical_upload(self, job: Job, path: Path):
        payloads = Chunker.get_or_create(self.db, job)
        md5sum = job.source.md5sum

        total = len(payloads)
        for idx, payload in enumerate(payloads):
            if payload.has_remote:
                continue

            message, part_md5 = self._upload_payload(
                job.source.type, md5sum, path, payload
            )

            with self.u_ctx.db.atomic():
                payload.md5sum = part_md5
                payload.save(only=[Payload.md5sum])
                RemotePayload.register_upload(payload, message, self.owner)

            if idx < total - 1:

                self._smart_pause()

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

    def resolve_naming_payload(self, payload: "Payload") -> Tuple[str, str]:
        if len(payload.filename) < self.max_filename_len:
            return payload.filename, ""

        return payload.filename_short, payload.filename

    def _upload_payload(
        self, source_type: SourceType, md5sum: str, path: Path, payload: Payload
    ):

        if source_type == SourceType.FOLDER:
            tape = tartape.Tape(path)
            volumen = tape.get_volume(
                payload.filename,
                payload.sequence_index,
                payload.start_offset,
                payload.end_offset,
            )
        else:
            volumen = FileVolume(
                path, payload.start_offset, payload.end_offset, payload.filename
            )

        limit_bytes = self.u_ctx.settings.upload_limit_rate_kbps * 1024
        with volumen:
            with ThrottledFile(volumen, limit_bytes) as doc_stream:
                filename, caption = self.resolve_naming_payload(payload)
                progress_callback = UploadProgress(payload.filename)
                tg_message = cast(
                    "Message",
                    self.client.send_document(
                        chat_id=self.tg_chat.id,
                        document=doc_stream,  # type: ignore
                        file_name=filename,
                        caption=caption,
                        progress=progress_callback,
                        force_document=True,
                    ),
                )
                progress_callback.finish()
                return tg_message, volumen.md5sum

    def _smart_forward_strategy(
        self,
        md5sum: str,
        payload_adopted: Payload,
        remote_mirror: RemotePayload,
    ) -> "Message":
        filename, caption = self.resolve_naming_payload(payload_adopted)
        return cast(
            "Message",
            self.client.send_document(
                chat_id=self.tg_chat.id,
                document=remote_mirror.message.document.file_id,
                file_name=filename,
                caption=caption,
            ),
        )
