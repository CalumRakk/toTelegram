import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple, cast

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import User

from rich.console import Console
from typer import confirm, prompt

from totelegram.core.enums import (
    AvailabilityState,
    DuplicatePolicy,
    Strategy,
)
from totelegram.core.setting import Settings
from totelegram.services.chunking import ChunkingService
from totelegram.services.discovery import DiscoveryService
from totelegram.store.database import db_proxy
from totelegram.store.models import Job, Payload, RemotePayload, TelegramUser
from totelegram.streams import open_upload_source

logger = logging.getLogger(__name__)
console = Console()


class UploadProgress:
    """Maneja el estado visual de la subida para Pyrogram."""

    def __init__(self, filename: str):
        self.filename = filename
        self.last_percentage = -1

    def __call__(self, current: int, total: int):
        percentage = int(current * 100 / total)
        if percentage % 5 == 0 and self.last_percentage != percentage:
            self.last_percentage = percentage
            logger.info(
                f"Subiendo {self.filename}: {current}/{total} bytes ({percentage}%)"
            )


class UploadService:
    def __init__(self, client: Client, settings: Settings):
        self.client = client
        self.settings = settings
        self.discovery = DiscoveryService(client)
        self.chunker = ChunkingService(settings)

        me = cast("User", client.get_me())
        self.current_user = TelegramUser.get_or_create_from_tg(me)

    def process_job(self, job: Job, policy_override: Optional[DuplicatePolicy] = None):
        """Orquestador principal del ciclo de vida de una subida."""
        policy = policy_override or self.settings.duplicate_policy

        # Fase de Descubrimiento
        state, source_remotes = self.discovery.investigate(job)

        # Toma la decisión final según Estado y Política
        if state == AvailabilityState.FULFILLED:
            console.print(
                f"[dim]✔ [bold]{job.source.path_str}[/bold] ya está disponible en el destino.[/dim]"
            )
            job.set_uploaded()
            return

        if state == AvailabilityState.RECOVERABLE and source_remotes:
            if policy == DuplicatePolicy.STRICT:
                console.print(
                    f"[yellow]STRICT:[/yellow] Archivo omitido (ya existe en el ecosistema)."
                )
                return

            if policy == DuplicatePolicy.SMART:
                console.print(
                    f"\n[bold blue]INFO:[/bold blue] El archivo ya está en el ecosistema (Chat: {source_remotes[0].chat_id})."
                )
                choice = prompt(
                    "¿Qué deseas hacer? [f] Reenviar / [u] Subir de nuevo / [s] Saltar",
                    default="f",
                ).lower()

                if choice == "f":
                    return self._execute_smart_forward(job, source_remotes)
                elif choice == "s":
                    console.print("[dim]Operación saltada.[/dim]")
                    return
            else:  # policy == OVERWRITE
                pass

        if (
            state == AvailabilityState.RESTRICTED
            and policy != DuplicatePolicy.OVERWRITE
        ):
            console.print(
                f"[yellow]AVISO:[/yellow] El archivo existe en otros perfiles pero tú no tienes acceso."
            )
            if not confirm("¿Deseas realizar una subida física propia?", default=True):
                return

        # Ejecución: Subida Física
        return self._execute_physical_upload(job)

    def _execute_smart_forward(self, job: Job, source_remotes: List[RemotePayload]):
        """Realiza un Smart Forward de los mensajes existentes en lugar de subir bytes.

        Si el reenvío falla, cae en subida física como fallback.
        Args:
            job: El trabajo (Job) a procesar.
            source_remotes: Lista de RemotePayloads fuente para el reenvío.
        Returns:
            None
        raises:
            Exception: Si el reenvío falla.
        """
        msg_ids = [r.message_id for r in source_remotes]
        from_chat_id = source_remotes[0].chat_id

        payloads = self.chunker.process_job(job)
        try:
            forwarded_msgs = self.client.forward_messages(
                chat_id=job.chat.id, from_chat_id=from_chat_id, message_ids=msg_ids
            )

            messages = (
                forwarded_msgs if isinstance(forwarded_msgs, list) else [forwarded_msgs]
            )

            with db_proxy.atomic():
                for i, msg in enumerate(messages):
                    RemotePayload.register_forward(
                        payload=payloads[i],
                        tg_message=msg,
                        source_msg_id=msg_ids[i],
                        owner=self.current_user,
                    )
                    time.sleep(0.5)
                job.set_uploaded()

            console.print(
                f"[bold green]✔ Smart Forward completado[/bold green] ({len(messages)} partes)."
            )

        except Exception as e:
            logger.error(f"Fallo en Smart Forward: {e}")
            console.print(
                "[red]Error en reenvío. Intentando subida física fallback...[/red]"
            )
            return self._execute_physical_upload(job)

    def _execute_physical_upload(self, job: Job):
        """Maneja la transferencia real de bytes a Telegram."""
        # Esto genera los archivos .bin temporales si el job es CHUNKED
        payloads = self.chunker.process_job(job)
        console.print(
            f"Iniciando subida física: [bold]{job.source.path_str}[/bold] ({len(payloads)} partes)"
        )

        for payload in payloads:
            if RemotePayload.select().where(RemotePayload.payload == payload).exists():
                logger.debug(f"Parte {payload.sequence_index} ya subida. Saltando.")
                continue

            self._upload_single_payload(payload)

        job.set_uploaded()
        console.print(f"[bold green]✔ Subida finalizada con éxito.[/bold green]")

    def _upload_single_payload(self, payload: Payload):
        """Sube un archivo individual (trozo o archivo único) a Telegram."""
        if not payload.path.exists():
            raise FileNotFoundError(f"Archivo físico no encontrado: {payload.path}")

        filename, caption = self._build_tg_metadata(payload)

        with open_upload_source(
            payload.path, self.settings.upload_limit_rate_kbps
        ) as doc_stream:
            progress = UploadProgress(filename)

            try:
                tg_message = self.client.send_document(
                    chat_id=payload.job.chat.id,
                    document=doc_stream,
                    file_name=filename,
                    caption=caption,  # type: ignore
                    progress=progress,
                )

                RemotePayload.register_upload(
                    payload=payload, tg_message=tg_message, owner=self.current_user
                )

                if payload.job.strategy == Strategy.CHUNKED:
                    self._cleanup_temp_payload(payload.path)

            except Exception as e:
                logger.error(f"Error crítico subiendo payload {payload.id}: {e}")
                raise e

    def _build_tg_metadata(self, payload: Payload) -> Tuple[str, Optional[str]]:
        """Determina el nombre y descripción para la UI de Telegram.

        - Si es CHUNKED, el nombre real del archivo en disco incluye el índice (ej: video.part1)
        - Si es SINGLE, usamos el nombre original o MD5 si es muy largo
        """
        original_name = Path(payload.job.source.path_str).name

        filename = payload.path.name
        caption = None

        if len(filename) >= self.settings.max_filename_length:
            suffix = Path(filename).suffix
            filename = f"{payload.md5sum}{suffix}"
            caption = original_name

        return filename, caption

    def _cleanup_temp_payload(self, path: Path):
        """Elimina archivos fragmentados tras una subida exitosa."""
        try:
            path.unlink(missing_ok=True)
            logger.debug(f"Temporal eliminado: {path.name}")
        except Exception as e:
            logger.warning(f"No se pudo eliminar temporal {path}: {e}")
