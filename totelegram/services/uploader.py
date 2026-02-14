import logging
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, cast

from tartape import TarTape
from totelegram.console import UI
from totelegram.services.tar_stream import TapeInspector, TarVolume
from totelegram.store.database import db_proxy
from totelegram.utils import batched

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import User, Message

from rich.console import Console

from totelegram.core.enums import (
    Strategy,
)
from totelegram.services.chunking import ChunkingService
from totelegram.services.discovery import DiscoveryService
from totelegram.store.models import (
    ArchiveEntry,
    Job,
    Payload,
    RemotePayload,
    TelegramUser,
)
from totelegram.streams import open_upload_source

logger = logging.getLogger(__name__)
console = Console()


class UploadProgress:
    def __init__(self, filename: str, is_chunk: bool = False, part_index: int = 0):
        self.filename = filename
        self.is_chunk = is_chunk
        self.part_index = part_index
        self.last_percentage = -1

    def __call__(self, current: int, total: int):
        percentage = int(current * 100 / total)

        # reporta cada 1%
        if percentage % 1 == 0 and self.last_percentage != percentage:
            self.last_percentage = percentage

            if self.is_chunk:
                prefix = f"[dim]Parte {self.part_index+1}[/dim]"
            else:
                prefix = "Enviando"

            UI.info(f"{prefix} [cyan]{self.filename}[/]: {percentage}%", end="\r")


class UploadService:
    def __init__(
        self,
        client: "Client",
        chunk_service: ChunkingService,
        upload_limit_rate_kbps: int = 0,
        max_filename_length: int = 60,
        discovery: Optional[DiscoveryService] = None,
    ):

        self.client = client
        self.chunker = chunk_service
        self.limit_rate_kbps = upload_limit_rate_kbps
        self.max_filename_len = max_filename_length

        self.discovery = discovery or DiscoveryService(client)

        me = cast("User", client.get_me())
        self.current_user = TelegramUser.get_or_create_from_tg(me)

    def execute_upload_strategy(self, job: Job):
        """
        Despachador Principal: Decide si sube archivos físicos o volúmenes virtuales.
        """
        if job.payloads.count() == 0:
            payloads = self.chunker.process_job(job)
        else:
            payloads = list(job.payloads.order_by(Payload.sequence_index))

        count = len(payloads)
        label = "volúmenes virtuales" if job.source.is_folder else "fragmentos"

        if job.strategy == Strategy.SINGLE:
            UI.info(f"Subiendo archivo único...")
        else:
            UI.info(f"Iniciando subida de {count} {label}...")

        for idx, payload in enumerate(payloads):
            if RemotePayload.select().where(RemotePayload.payload == payload).exists():
                logger.debug(f"Parte {idx} ya subida. Saltando.")
                continue

            if payload.temp_path and payload.temp_path.startswith("virtual://"):
                self._upload_virtual_payload(job, payload, idx, count)
            else:
                self._upload_physical_payload(job, payload, idx, count)

        job.set_uploaded()
        UI.success("Subida completa.")

    def _upload_physical_payload(
        self, job: Job, payload: Payload, index: int, total: int
    ):
        """
        Lógica clásica para archivos que existen en disco.
        """
        path = Path(payload.temp_path)
        if not path.exists():
            if job.strategy == Strategy.CHUNKED:
                console.print(f"[yellow]Reconstruyendo pieza {index}...[/yellow]")
                self.chunker.split_file_for_missing_payload(job, payload)
            else:
                raise FileNotFoundError(f"Archivo origen perdido: {path}")

        filename, caption = self._build_tg_metadata(payload)
        is_chunk = job.strategy == Strategy.CHUNKED

        with open_upload_source(path, self.limit_rate_kbps) as doc_stream:
            progress = UploadProgress(filename, is_chunk=is_chunk, part_index=index)

            tg_message = self.client.send_document(
                chat_id=job.chat.id,
                document=doc_stream,
                file_name=filename,
                caption=caption or "",
                progress=progress,
            )

            RemotePayload.register_upload(
                payload=payload, tg_message=tg_message, owner=self.current_user
            )

            if is_chunk:
                self._cleanup_temp_payload(path)

    def _upload_virtual_payload(
        self, job: Job, payload: Payload, index: int, total: int
    ):
        """
        Maneja la subida de carpetas usando TarVolumeStream.
        """
        if not job.source.inventory:
            raise ValueError(
                "El Job es de tipo carpeta pero no tiene inventario asociado."
            )
        # Se ocupa la DB del inventario
        # TODO: buscar una solucion para mejorar la necesita de la ruta de db tartape
        tape = TarTape(index_path=str(job.source.inventory.db_path))

        start_offset = payload.sequence_index * job.config.tg_max_size

        total = TapeInspector.get_total_size(tape)
        stream = TarVolume(
            tape=tape,
            start_offset=start_offset,
            max_volume_size=payload.size,
            total_tape_size=total,
            vol_index=payload.sequence_index,
        )

        folder_name = Path(job.source.path_str).name
        filename = f"{folder_name}.tar.{str(index + 1).zfill(3)}"
        caption = f"Volumen {index + 1}/{total} de {folder_name}"

        progress = UploadProgress(filename, is_chunk=True, part_index=index)

        try:
            tg_message = self.client.send_document(
                chat_id=job.chat.id,
                document=stream,  # type: ignore
                file_name=filename,
                caption=caption,
                progress=progress,
            )

            real_md5, entries = stream.get_completed_files()

            with db_proxy.atomic():
                payload.md5sum = real_md5
                payload.save()

                RemotePayload.register_upload(
                    payload=payload, tg_message=tg_message, owner=self.current_user
                )

                entries_to_create = []
                for entry in entries:
                    entries_to_create.append(
                        {
                            "source": job.source,
                            "relative_path": entry["path"],
                            "start_offset": entry["start_offset"],
                            "end_offset": entry["end_offset"],
                            "start_volume_index": entry["start_vol"],
                            "end_volume_index": entry["end_vol"],
                        }
                    )

                if entries_to_create:
                    ArchiveEntry.insert_many(entries_to_create).execute()

            logger.debug(f"Volumen virtual {index} subido y commiteado.")

        except Exception as e:
            logger.error(f"Fallo subiendo volumen virtual {index}: {e}")
            raise e
        finally:
            stream.close()

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
            # Si no hay payloads, es la primera vez que encontramos este job para forward
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
                f"[bold green][OK] Smart Forward completado con éxito.[/bold green]"
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
        if job.payloads.count() == 0:
            payloads = self.chunker.process_job(job)
        else:
            payloads = list(job.payloads.order_by(Payload.sequence_index))

        if job.strategy == Strategy.SINGLE:
            UI.info(f"Subiendo el archivo físico...")
        else:
            UI.info(f"Iniciando subida de {len(payloads)} fragmentos...")

        for idx, payload in enumerate(payloads):
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

            self._upload_single_payload(
                payload, is_chunk=(job.strategy == Strategy.CHUNKED), index=idx
            )

        job.set_uploaded()
        UI.success("Subida completa.")

    def _upload_single_payload(
        self, payload: Payload, is_chunk: bool = False, index: int = 0
    ):
        """Sube un archivo individual (trozo o archivo único) a Telegram."""
        if not payload.path.exists():
            raise FileNotFoundError(f"Archivo físico no encontrado: {payload.path}")

        filename, caption = self._build_tg_metadata(payload)

        with open_upload_source(payload.path, self.limit_rate_kbps) as doc_stream:
            progress = UploadProgress(filename, is_chunk=is_chunk, part_index=index)

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

        if len(filename) >= self.max_filename_len:
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
