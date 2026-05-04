import logging
import random
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Optional, Tuple, cast

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
from totelegram.packaging import Chunker, SnapshotService
from totelegram.schemas import (
    AvailabilityState,
    JobStatus,
    ProgressState,
    SourceType,
)
from totelegram.stream import FileVolume
from totelegram.types import AvailabilityReport, UploadContext
from totelegram.utils import ThrottledFile

if TYPE_CHECKING:
    from pyrogram.types import Message


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

    def process_job(self, job: Job, path: Path, is_last_job: bool = False):
        """
        Orquesta el ciclo de vida de un Job: Investigación, Ejecución y Cierre.
        Este es el 'cerebro' que decide si reenviar, subir o marcar como completado.
        """
        logger.info(f"Iniciando procesamiento de Job {job.id} para: {path.name}")
        report = self.u_ctx.discovery.investigate(job)

        if report.state == AvailabilityState.FULFILLED:
            UI.info(f"[dim]{path.name}[/] ya está disponible en el destino.")
            if job.status != JobStatus.UPLOADED:
                job.set_uploaded()

        elif report.state == AvailabilityState.NEEDS_UPLOAD:
            self.execute_physical_upload(job, path, is_last_job)

        elif report.state == AvailabilityState.CAN_FORWARD:
            UI.info("Recurso encontrado en otro chat. Iniciando [bold]Smart Forward[/]...")
            self.execute_smart_forward(job, report)

        else:
            raise ValueError(f"Estado de disponibilidad no reconocido: {report.state}")

        job = Job.get_by_id(job.id)
        logger.info(f"Evaluando cierre del Job {job.id}. Estado actual en DB: {job.status}")

        if job.status == JobStatus.UPLOADED:
            try:
                logger.info(f"Generando Snapshot para el Job {job.id}...")
                SnapshotService.generate_snapshot(job)
                logger.info("Snapshot generado y guardado con éxito.")
                return True
            except Exception as e:
                logger.error(f"Fallo catastrófico generando el Snapshot: {e}", exc_info=True)
                raise e

        logger.info(f"El Job {job.id} todavía no está completado, saltando Snapshot.")
        return False

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

    def _claim_next_payload(self, job: Job) -> Optional[Tuple[Payload, FileLock]]:
            """Busca y bloquea a nivel de SO la siguiente pieza disponible."""
            logger.debug(f"Buscando siguiente pieza disponible para Job {job.id}...")

            valid_remotes = RemotePayload.select().where(
                (RemotePayload.payload == Payload.id) &
                (RemotePayload.is_orphaned == False) # noqa: E712
            )

            pending_payloads = (
                Payload.select()
                .where(
                    (Payload.job == job) &
                    (~peewee.fn.EXISTS(valid_remotes))
                )
                .order_by(Payload.sequence_index)
            )

            lock_dir = self.manager.worktable / "locks" / f"job_{job.id}"
            lock_dir.mkdir(parents=True, exist_ok=True)

            for payload in pending_payloads:
                lock_path = lock_dir / f"payload_{payload.id}.lock"
                lock = FileLock(lock_path, timeout=0)

                try:
                    lock.acquire()
                    logger.info(
                        f"Pieza {payload.sequence_index} reclamada (OS Lock) por {self.profile_name}"
                    )
                    return payload, lock  # type:ignore - Retornamos el payload y el candado activo
                except Timeout:
                    # El archivo ya está bloqueado por otro proceso, pasamos al siguiente
                    continue

            return None
    def _smart_pause(self):
        """Calcula y ejecuta una pausa aleatoria basada en la configuración."""
        r = self.settings.upload_pause_range

        minutes = random.randint(min(r), max(r))

        if minutes > 0:
            UI.sleep_progress(minutes * 60)

    def execute_physical_upload(self, job: Job, path: Path, is_last_job: bool):
            logger.info(
                f"Iniciando subida física de {path.name}. Estrategia: {job.strategy}"
            )
            with self.db.atomic():
                Chunker.get_or_create(job)

            md5sum = job.source.md5sum
            while True:
                claim_result = self._claim_next_payload(job)

                if claim_result is None:
                    break # No hay más piezas disponibles (subidas o procesándose)

                payload, lock = claim_result

                # MAGIA: El lock se mantendrá solo dentro de este bloque 'with'
                with lock:
                    UI.info(f"Subiendo la pieza [bold]{payload.filename}[/]")

                    try:
                        message, part_md5 = self._upload_payload(
                            job.source.type, md5sum, path, payload
                        )

                        with self.db.atomic():
                            # Actualizamos el md5sum en vez de usar set_uploaded()
                            payload.md5sum = part_md5
                            payload.save(only=[Payload.md5sum, Payload.updated_at])

                            RemotePayload.register_upload(payload, message, self.owner)

                        UI.success("Pieza subida exitosamente.")

                        has_more_payloads = Payload.total_pending_for_job(job) > 0
                        if has_more_payloads and not is_last_job:
                            self._smart_pause()

                    except Exception as e:
                        # El bloque 'with lock' finaliza y el SO libera el archivo automáticamente.
                        raise e

            # Fuera del bucle (terminó la subida o la cola):
            with self.db.atomic():
                pending = Payload.total_pending_for_job(job)
                logger.info(f"Evaluando piezas pending para el Job {job.id}: quedan {pending}")

                if pending == 0:
                    logger.info(f"Marcando Job {job.id} como UPLOADED en la base de datos.")
                    job.set_uploaded()
                    UI.success("¡Subida completa! Todas las piezas están en Telegram.")

                    # Limpieza: Borramos la carpeta de locks porque ya no se necesita
                    lock_dir = self.manager.worktable / "locks" / f"job_{job.id}"
                    if lock_dir.exists():
                        shutil.rmtree(lock_dir, ignore_errors=True)
                else:
                    logger.info(f"Worker terminó su cola, pero faltan {pending} piezas que otro worker está subiendo.")
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

        state_control = ProgressState()
        progress = Progress(
            TextColumn("[bold blue]{task.fields[filename]}", justify="left"),
            BarColumn(bar_width=20, pulse_style="white"),
            "[progress.percentage]{task.percentage:>3.0f}%",
            DownloadColumn(),
            TransferSpeedColumn(),
            TextColumn("{task.fields[status]}"),
            TimeRemainingColumn(),
            console=console,
            transient=True,
            expand=False,
        )

        def update_rich_progress(current, total, state: ProgressState):
            progress.update(task_id, completed=current, status=state.status)

        logger.debug(f"Preparando stream de datos para pieza {payload.sequence_index}")
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
            task_id = progress.add_task(
                "upload",
                total=payload.size,
                filename=filename,
                status=state_control.status,
            )

            with volumen:
                logger.info(
                    f"Transmitiendo pieza {payload.filename} a Telegram (Tamaño: {payload.size} bytes)"
                )

                with ThrottledFile(volumen, limit_bytes) as doc_stream:
                    doc_stream= cast(BinaryIO, doc_stream )

                    tg_message = cast(
                        "Message",
                        self.client.send_document(
                            chat_id=self.tg_chat.id,
                            document=doc_stream,
                            file_name=filename,
                            caption=caption,
                            progress=update_rich_progress,
                            force_document=True,
                            progress_args=(state_control,),
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
