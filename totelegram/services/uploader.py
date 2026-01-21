import logging
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, cast

from totelegram.store.database import db_proxy
from totelegram.utils import batched

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import User, Message

from rich.console import Console

from totelegram.core.enums import (
    Strategy,
)
from totelegram.core.setting import Settings
from totelegram.services.chunking import ChunkingService
from totelegram.services.discovery import DiscoveryService
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

    def execute_smart_forward(self, job: Job, source_remotes: List[RemotePayload]):
        """
        Reenvia de forma atomica los RemotePayloads específicados al chat de destino que contiene el job.

        Args:
            job (Job): El job a subir.
            source_remotes (List[RemotePayload]): Los RemotePayloads de origen. pueden venir de múltiples chats.

        Raises:
            Exception: Si ocurre un error durante el proceso, se realiza un rollback.

        La tarea de esta funcion es recolectar piezas dispersas y reenviarlas al chat destino de forma atomica.
        """
        if job.payloads.count() < 1:
            # Si no hay payloads, es la primera vez que estrucutramos este job para forward
            # Debemos crearlos para tener el mapeo MD5 -> secuencia en este chain
            self.chunker.process_job(job)

        # Supongo que el limite de forward_messages es ~200, pero uso 100 por seguridad.
        API_CHUNK_LIMIT = 100

        # Esto asegura que el mensaje 1 sea la parte 1.
        target_payloads: List[Payload] = list(
            job.payloads.order_by(Payload.sequence_index)
        )

        groups: Dict[int, List[RemotePayload]] = defaultdict(list)
        for r in source_remotes:
            groups[r.chat_id].append(r)

        forwarded_history = []  # Para el Rollback: guardamos dict chat:list[message_id]
        registrations = []

        # TODO: documenta las siguientes garantias de seguridad:
        # 1. lOS Payloads del Job se crean en orden secuelcial (0, 1, 2...)
        # 2. La lista de target_payloads se obtiene directamente de la relación job.payloads asegurando el orden secuencial.
        # La lista del batch tambén se obtiene en orden secuencial.

        try:
            for from_chat_id, remote_payloads in groups.items():
                # Ordena los remotes por sequence_index
                # para que el forward mantenga el orden del archivo original.
                remote_payloads.sort(key=lambda x: x.payload.sequence_index)

                for batch in batched(remote_payloads, API_CHUNK_LIMIT):
                    batch: List[RemotePayload]
                    batch_ids = [r.message_id for r in batch]

                    # Ejecutar Reenvío
                    # Confiamos que la API de Telegram, los mensajes se reenvían y devuelven
                    # siguiendo el orden de la lista de IDs especificada.
                    res = self.client.forward_messages(
                        chat_id=job.chat.id,
                        from_chat_id=from_chat_id,
                        message_ids=batch_ids,
                    )

                    # Guardamos el ID de los mensajes reenviados
                    new_msgs: List[Message] = res if isinstance(res, list) else [res]  # type: ignore
                    msg_ids = [m.id for m in new_msgs]

                    forwarded_history.append(msg_ids)

                    for index, msg in enumerate(new_msgs):
                        # Mapeo por posición: Confio que Telegram y la creacion de payloads garantizan el mismo orden.
                        original_seq = batch[index].payload.sequence_index

                        # Buscamos el payload correspondiente en NUESTRO job
                        target_payload = next(
                            p
                            for p in target_payloads
                            if p.sequence_index == original_seq
                        )
                        registrations.append((target_payload, msg))

            with db_proxy.atomic():
                for payload, msg in registrations:
                    RemotePayload.register_upload(
                        payload=payload, tg_message=msg, owner=self.current_user
                    )
                job.set_uploaded()

            console.print(
                f"[bold green]✔ Smart Forward completado con éxito.[/bold green]"
            )
        except Exception as e:
            logger.error(f"Error en reenvío inteligente: {e}. Limpiando destino...")
            for batch_ids in forwarded_history:
                for sub_batch in batched(batch_ids, 100):
                    try:
                        self.client.delete_messages(job.chat.id, sub_batch)  # type: ignore
                    except:
                        pass
            raise e

    def execute_physical_upload(self, job: Job):
        """Realiza la subida fisica de un Job."""

        # Esto genera los archivos .bin temporales si el job es CHUNKED
        if job.payloads.count() == 0:
            payloads = self.chunker.process_job(job)
        else:
            payloads = list(job.payloads.order_by(Payload.sequence_index))

        console.print(
            f"Subida física: [bold]{job.source.path_str}[/bold] ({len(payloads)} partes)"
        )

        for payload in payloads:
            # Verificar si esta parte ya se subió (por si el job se interrumpió a la mitad)
            if RemotePayload.select().where(RemotePayload.payload == payload).exists():
                logger.debug(f"Parte {payload.sequence_index} ya subida. Saltando.")
                continue
            if not payload.path.exists():
                if job.strategy == Strategy.CHUNKED:
                    console.print(
                        f"[yellow] Pieza {payload.sequence_index} desaparecida. Re-generando...[/yellow]"
                    )
                    self.chunker.split_file_for_missing_payload(job, payload)
                else:
                    # Si es SINGLE y no existe, el archivo origen desapareció de su ruta
                    raise FileNotFoundError(
                        f"El archivo original desaparecio mientras se subia: {payload.path}"
                    )

            self._upload_single_payload(payload)

        job.set_uploaded()

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
