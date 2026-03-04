import logging
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, cast

import tartape
from tartape import Tape
from tartape.schemas import ManifestEntry

from totelegram.cli.ui.console import UI
from totelegram.common.utils import batched
from totelegram.manager.database import db_proxy

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import User, Message

from rich.console import Console

from totelegram.common.enums import (
    AvailabilityState,
    Strategy,
)
from totelegram.common.streams import open_upload_source
from totelegram.logic.chunker import ChunkingService
from totelegram.logic.discovery import DiscoveryService
from totelegram.manager.models import (
    Job,
    Payload,
    RemotePayload,
    SourceFile,
    TapeMember,
    TelegramUser,
)

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
        self._dispatch_map: Dict[AvailabilityState, Callable[[Job, Any], None]] = {
            AvailabilityState.SYSTEM_NEW: self._handle_new,
            AvailabilityState.FULFILLED: self._handle_fulfilled,
            AvailabilityState.REMOTE_MIRROR: self._handle_smart_forward_strategy,
            AvailabilityState.REMOTE_PUZZLE: self._handle_smart_forward_strategy,
            AvailabilityState.REMOTE_RESTRICTED: self._handle_restricted,
        }

    def run(self, job: Job, discovery_report: Any):
        """
        Punto único de entrada para ejecutar la estrategia según el reporte.
        """
        handler = self._dispatch_map.get(discovery_report.state)
        if not handler:
            raise NotImplementedError(
                f"Estrategia para {discovery_report.state} no implementada."
            )

        handler(job, discovery_report)

    def _handle_new(self, job: Job, report: Any):
        UI.info("Iniciando subida de nuevos volúmenes...")
        self.execute_upload_strategy(job)

    def _handle_fulfilled(self, job: Job, report: Any):
        UI.success("¡Operación completada! Todos los volúmenes ya están en el destino.")
        job.set_uploaded()

    def _handle_smart_forward_strategy(self, job: Job, report: Any):
        UI.info(
            "Carpeta encontrada en el ecosistema. Clonando volúmenes (Smart Forward)..."
        )
        if report.remotes:
            self._execute_smart_forward(job, report.remotes)
        else:
            UI.error("Error al localizar las piezas en la red.")

    def _handle_restricted(self, job: Job, report: Any):
        UI.warn(
            "Se detectaron registros pero los archivos no son accesibles. Re-subiendo..."
        )
        self.execute_upload_strategy(job)

    def execute_upload_strategy(self, job: Job):
        if job.payloads.count() == 0:
            payloads = self.chunker.process_job(job)
        else:
            payloads = list(job.payloads.order_by(Payload.sequence_index))

        if job.source.is_folder:
            with tartape.open(job.source.path_str) as tape:
                self._process_archive_volumes(job, payloads, tape)
        else:
            for idx, payload in enumerate(payloads):
                if (
                    RemotePayload.select()
                    .where(RemotePayload.payload == payload)
                    .exists()
                ):
                    continue
                self._upload_physical_payload(job, payload, idx, len(payloads))

        job.set_uploaded()

    def _process_archive_volumes(self, job: Job, payloads: List[Payload], tape: Tape):
        fingerprint = job.source.md5sum
        folder_name = Path(job.source.path_str).name
        vol_size = job.config.tg_max_size
        total_volumes = len(payloads)

        naming_template = (
            "{name}_{part}-{total}.tar"
            if len(folder_name) <= self.max_filename_len
            else fingerprint + "_{part}-{total}"
        )
        for v_idx, (volume, manifest) in enumerate(
            tape.iter_volumes(size=vol_size, naming_template=naming_template)
        ):
            current_payload = payloads[v_idx]

            if current_payload.remote_exists:
                logger.debug(f"Volumen {v_idx + 1} ya está en Telegram. Saltando...")
                continue

            filename = f"{Path(job.source.path_str).name}.tar.{str(v_idx + 1).zfill(3)}"
            progress = UploadProgress(filename, is_chunk=True, part_index=v_idx)

            UI.info(f"Subiendo volumen {v_idx + 1}/{total_volumes}...")

            try:
                tg_message = cast(
                    "Message",
                    self.client.send_document(
                        chat_id=job.chat.id,
                        document=volume,  # type: ignore
                        caption=f"Volumen {v_idx + 1} de {Path(job.source.path_str).name}",
                        progress=progress,
                    ),
                )

                self._commit_volume_success(
                    job.source,
                    current_payload,
                    tg_message,
                    manifest.entries,
                    volume.md5sum,
                )

            except Exception as e:
                logger.error(f"Fallo en volumen {v_idx + 1}: {e}")
                raise e

    def _commit_volume_success(
        self,
        source: SourceFile,
        payload: Payload,
        tg_message: "Message",
        manifest: List[ManifestEntry],
        md5sum: str,
    ):
        with db_proxy.atomic():
            payload.md5sum = md5sum
            payload.save(only=[Payload.md5sum])
            RemotePayload.register_upload(
                payload=payload, tg_message=tg_message, owner=self.current_user
            )
            TapeMember.register_manifest_entries(
                source=source, payload=payload, entries=manifest
            )

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

    def _execute_smart_forward(self, job: Job, source_remotes: List[RemotePayload]):
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

    def _execute_physical_upload(self, job: Job):
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
