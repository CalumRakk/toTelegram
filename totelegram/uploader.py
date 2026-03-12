import logging
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional, Tuple, cast

import peewee
import tartape
from filelock import FileLock, Timeout
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from totelegram.cli.ui import UI, console
from totelegram.models import Job, Payload, RemotePayload
from totelegram.packaging import Chunker
from totelegram.schemas import PayloadStatus, SourceType
from totelegram.stream import FileVolume
from totelegram.types import AvailabilityReport, UploadContext
from totelegram.utils import ThrottledFile

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Message, User


logger = logging.getLogger(__name__)


class UploadService:
    # TODO: Luego de consolidar la logica. Hay que sacar los UI de aqui.
    def __init__(
        self,
        u_ctx: UploadContext,
    ):
        self.client = u_ctx.client
        self.limit_rate_kbps = u_ctx.settings.upload_limit_rate_kbps
        self.max_filename_len = u_ctx.settings.max_filename_length
        self.profile_name = u_ctx.settings.profile_name
        self.manager = u_ctx.state.manager

        self.settings = u_ctx.settings
        self.client = u_ctx.client
        self.db = u_ctx.db
        self.u_ctx = u_ctx
        self.owner = u_ctx.owner
        self.tg_chat = u_ctx.tg_chat

    def _is_worker_alive(self, profile_name: str) -> bool:
        """Verifica mediante el archivo .lock si el worker/perfil sigue activo."""
        lock_path = self.manager.profiles_dir / f"{profile_name}.lock"
        if not lock_path.exists():
            return False

        lock = FileLock(lock_path, timeout=0)
        try:
            with lock.acquire(timeout=0.01):
                return False
        except Timeout:
            return True

    def _claim_next_payload(self, job: Job) -> Optional[Payload]:
        max_retries = 5
        for attempt in range(max_retries):
            try:
                with self.db.atomic():
                    # Liberamos las piezas abandonadas
                    stale_payloads = cast(
                        Iterable[Payload],
                        Payload.select().where(
                            (Payload.job == job)
                            & (Payload.status == PayloadStatus.CLAIMED)
                            & (Payload.claimed_by != self.profile_name)
                        ),
                    )

                    for p in stale_payloads:
                        if p.claimed_by is None:
                            p.release()
                        elif not self._is_worker_alive(p.claimed_by):
                            logger.info(
                                f"Liberando pieza {p.sequence_index} abandonada por {p.claimed_by}"
                            )
                            p.release()

                    # Reclamamos la siguiente pieza PENDING
                    next_available_id = (
                        Payload.select(Payload.id)
                        .where(
                            (Payload.job == job)
                            & (
                                (Payload.status == PayloadStatus.PENDING) |
                                ((Payload.status == PayloadStatus.CLAIMED) & (Payload.claimed_by == self.profile_name))
                            )
                        )
                        .order_by(Payload.sequence_index)
                        .limit(1)
                    )

                    query = Payload.update(
                        status=PayloadStatus.CLAIMED,
                        claimed_by=self.profile_name
                    ).where(Payload.id == next_available_id)

                    rows_affected = query.execute()

                    if rows_affected > 0:
                        return Payload.get(Payload.id == next_available_id)
                return None

            except peewee.OperationalError as e:
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    wait_time = (2**attempt) * 0.1  # 0.1s, 0.2s, 0.4s...
                    time.sleep(wait_time)
                    continue
                raise e

    def _smart_pause(self):
        """Calcula y ejecuta una pausa aleatoria basada en la configuración."""
        r = self.settings.upload_pause_range

        minutes = random.randint(min(r), max(r))

        if minutes > 0:
            UI.sleep_progress(minutes * 60)

    def execute_physical_upload(self, job: Job, path: Path):

        with self.db.atomic():
            Chunker.get_or_create(job)

        md5sum = job.source.md5sum
        while True:
            payload = self._claim_next_payload(job)

            if payload is None:
                break

            UI.info(
                f"Reclamada pieza [bold]{payload.sequence_index}[/] por [cyan]{self.profile_name}[/]"
            )

            try:
                message, part_md5 = self._upload_payload(
                    job.source.type, md5sum, path, payload
                )

                with self.db.atomic():
                    payload.set_uploaded(part_md5)
                    RemotePayload.register_upload(payload, message, self.owner)

                # if idx < total - 1:
                #     self._smart_pause()

            except Exception as e:
                with self.db.atomic():
                    payload.release()
                raise e

        with self.db.atomic():
            total_pending = (
                Payload.select()
                .where(
                    (Payload.job == job) & (Payload.status != PayloadStatus.UPLOADED)
                )
                .count()
            )
            if total_pending == 0:
                job.set_uploaded()
                UI.success("¡Subida completa! Todas las piezas están en Telegram.")

    def execute_smart_forward(self, job: Job, report: AvailabilityReport):
        mirrros = {r.payload.sequence_index: r for r in report.remotes}
        UI.info(f"Reenviando {len(mirrros)} partes...")

        with self.db.atomic():
            job_adopted = job.adopt_job(report.remotes[0].payload.job)
            md5sum = job_adopted.source.md5sum
            payloads = Chunker.get_or_create(job_adopted)

        for payload_adopted in payloads:
            if payload_adopted.has_remote:
                continue

            remote_mirror = mirrros[payload_adopted.sequence_index]
            message = self._smart_forward_strategy(
                md5sum, payload_adopted, remote_mirror
            )
            with self.db.atomic():
                RemotePayload.register_upload(payload_adopted, message, self.owner)

            time.sleep(1)

        with self.db.atomic():
            job_adopted.set_uploaded()

    def resolve_naming_payload(self, payload: "Payload") -> Tuple[str, str]:
        if len(payload.filename) < self.max_filename_len:
            return payload.filename, ""

        return payload.filename_short, payload.filename

    def _upload_payload(
        self, source_type: SourceType, md5sum: str, path: Path, payload: Payload
    ):

        progress = Progress(
            TextColumn("[bold blue]{task.fields[filename]}", justify="left"),
            BarColumn(bar_width=20, pulse_style="white"),
            "[progress.percentage]{task.percentage:>3.0f}%",
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
            expand=False,
        )

        def update_rich_progress(current, total):
            progress.update(task_id, completed=current)

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
        filename, caption = self.resolve_naming_payload(payload)
        with progress:
            task_id = progress.add_task("upload", total=payload.size, filename=filename)

            with volumen:
                with ThrottledFile(volumen, limit_bytes) as doc_stream:
                    tg_message = cast(
                        "Message",
                        self.client.send_document(
                            chat_id=self.tg_chat.id,
                            document=doc_stream,  # type: ignore
                            file_name=filename,
                            caption=caption,
                            progress=update_rich_progress,
                            force_document=True,
                        ),
                    )
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
