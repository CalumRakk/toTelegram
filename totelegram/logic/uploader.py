import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Tuple, cast

from totelegram.cli.ui.console import UI
from totelegram.common.types import AvailabilityReport
from totelegram.logic.chunker import Chunker

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import User, Message
    from totelegram.cli.commands.upload import UploadContext

from totelegram.common.streams import VirtualFileStream, open_upload_source
from totelegram.manager.models import (
    Job,
    Payload,
    RemotePayload,
)

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
        self.u_ctx = u_ctx
        self.client = u_ctx.client
        self.limit_rate_kbps = u_ctx.settings.upload_limit_rate_kbps
        self.max_filename_len = u_ctx.settings.max_filename_length

    def execute_physical_upload(self, job: Job, path: Path):
        payloads = Chunker.get_or_create(self.u_ctx.db, job)
        md5sum = job.source.md5sum
        for payload in payloads:
            if payload.has_remote:
                continue

            message = self._upload_payload(md5sum, path, payload)
            RemotePayload.register_upload(payload, message, self.u_ctx.owner)
        job.set_uploaded()

    def execute_smart_forward(self, job: Job, report: AvailabilityReport):
        mirrros = {r.payload.sequence_index: r for r in report.remotes}
        UI.info(f"Reenviando {len(mirrros)} partes...")

        job_adopted = job.adopt_job(report.remotes[0].payload.job)
        md5sum = job_adopted.source.md5sum
        for payload_adopted in Chunker.get_or_create(self.u_ctx.db, job_adopted):
            if payload_adopted.has_remote:
                continue

            remote_mirror = mirrros[payload_adopted.sequence_index]
            message = self._smart_forward_strategy(
                md5sum, payload_adopted, remote_mirror
            )
            RemotePayload.register_upload(payload_adopted, message, self.u_ctx.owner)
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

    def _upload_payload(self, md5sum: str, path: Path, payload: Payload):
        volumen = VirtualFileStream(
            path, start_offset=payload.start_offset, end_offset=payload.end_offset
        )

        with open_upload_source(volumen, self.limit_rate_kbps) as doc_stream:  # type: ignore
            filename, caption = self.resolve_naming_payload(md5sum, payload)
            tg_message = cast(
                "Message",
                self.client.send_document(
                    chat_id=self.u_ctx.tg_chat.id,
                    document=doc_stream,
                    file_name=filename,
                    caption=caption,
                    progress=UploadProgress(),
                ),
            )
            return tg_message

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
                chat_id=self.u_ctx.tg_chat.id,
                document=remote_mirror.message.document.file_id,
                file_name=filename,
                caption=caption,
            ),
        )

    # def _process_archive_volumes(self, job: Job, payloads: List[Payload], tape: Tape):
    #     fingerprint = job.source.md5sum
    #     folder_name = Path(job.source.path_str).name
    #     vol_size = job.config.tg_max_size
    #     total_volumes = len(payloads)

    #     naming_template = (
    #         "{name}_{part}-{total}.tar"
    #         if len(folder_name) <= self.max_filename_len
    #         else fingerprint + "_{part}-{total}"
    #     )
    #     for v_idx, (volume, manifest) in enumerate(
    #         tape.iter_volumes(size=vol_size, naming_template=naming_template)
    #     ):
    #         current_payload = payloads[v_idx]

    #         if current_payload.remote_exists:
    #             logger.debug(f"Volumen {v_idx + 1} ya está en Telegram. Saltando...")
    #             continue

    #         filename = f"{Path(job.source.path_str).name}.tar.{str(v_idx + 1).zfill(3)}"
    #         progress = UploadProgress(filename, is_chunk=True, part_index=v_idx)

    #         UI.info(f"Subiendo volumen {v_idx + 1}/{total_volumes}...")

    #         try:
    #             tg_message = cast(
    #                 "Message",
    #                 self.client.send_document(
    #                     chat_id=job.chat.id,
    #                     document=volume,  # type: ignore
    #                     caption=f"Volumen {v_idx + 1} de {Path(job.source.path_str).name}",
    #                     progress=progress,
    #                 ),
    #             )

    #             self._commit_volume_success(
    #                 job.source,
    #                 current_payload,
    #                 tg_message,
    #                 manifest.entries,
    #                 volume.md5sum,
    #             )

    #         except Exception as e:
    #             logger.error(f"Fallo en volumen {v_idx + 1}: {e}")
    #             raise e

    # def _commit_volume_success(
    #     self,
    #     source: SourceFile,
    #     payload: Payload,
    #     tg_message: "Message",
    #     manifest: List[ManifestEntry],
    #     md5sum: str,
    # ):
    #     with db_proxy.atomic():
    #         payload.md5sum = md5sum
    #         payload.save(only=[Payload.md5sum])
    #         RemotePayload.register_upload(
    #             payload=payload, tg_message=tg_message, owner=self.current_user
    #         )
    #         TapeMember.register_manifest_entries(
    #             source=source, payload=payload, entries=manifest
    #         )

    # def _execute_smart_forward(self, job: Job, source_remotes: List[RemotePayload]):
    #     """
    #     Reenvia de forma atomica los RemotePayloads específicados al chat de destino que contiene el job.

    #     Args:
    #         job (Job): El job a subir.
    #         source_remotes (List[RemotePayload]): Los RemotePayloads de origen. pueden venir de múltiples chats.

    #     Raises:
    #         Exception: Si ocurre un error durante el proceso, se realiza un rollback.

    #     La tarea de esta funcion es recolectar piezas dispersas y reenviarlas al chat destino de forma atomica.
    #     """
    #     if job.payloads.count() < 1:
    #         # Si no hay payloads, es la primera vez que encontramos este job para forward
    #         # Debemos crearlos para tener el mapeo MD5 -> secuencia en este chain
    #         self.chunker.process_job(job)

    #     # Supongo que el limite de forward_messages es ~200, pero uso 100 por seguridad.
    #     API_CHUNK_LIMIT = 100

    #     # Esto asegura que el mensaje 1 sea la parte 1.
    #     target_payloads: List[Payload] = list(
    #         job.payloads.order_by(Payload.sequence_index)
    #     )

    #     groups: Dict[int, List[RemotePayload]] = defaultdict(list)
    #     for r in source_remotes:
    #         groups[r.chat_id].append(r)

    #     forwarded_history = []  # Para el Rollback: guardamos dict chat:list[message_id]
    #     registrations = []

    #     # TODO: documenta las siguientes garantias de seguridad:
    #     # 1. lOS Payloads del Job se crean en orden secuelcial (0, 1, 2...)
    #     # 2. La lista de target_payloads se obtiene directamente de la relación job.payloads asegurando el orden secuencial.
    #     # La lista del batch tambén se obtiene en orden secuencial.

    #     try:
    #         for from_chat_id, remote_payloads in groups.items():
    #             # Ordena los remotes por sequence_index
    #             # para que el forward mantenga el orden del archivo original.
    #             remote_payloads.sort(key=lambda x: x.payload.sequence_index)

    #             for batch in batched(remote_payloads, API_CHUNK_LIMIT):
    #                 batch: List[RemotePayload]
    #                 batch_ids = [r.message_id for r in batch]

    #                 # Ejecutar Reenvío
    #                 # Confiamos que la API de Telegram, los mensajes se reenvían y devuelven
    #                 # siguiendo el orden de la lista de IDs especificada.
    #                 res = self.client.forward_messages(
    #                     chat_id=job.chat.id,
    #                     from_chat_id=from_chat_id,
    #                     message_ids=batch_ids,
    #                 )

    #                 # Guardamos el ID de los mensajes reenviados
    #                 new_msgs: List[Message] = res if isinstance(res, list) else [res]  # type: ignore
    #                 msg_ids = [m.id for m in new_msgs]

    #                 forwarded_history.append(msg_ids)

    #                 for index, msg in enumerate(new_msgs):
    #                     # Mapeo por posición: Confio que Telegram y la creacion de payloads garantizan el mismo orden.
    #                     original_seq = batch[index].payload.sequence_index

    #                     # Buscamos el payload correspondiente en NUESTRO job
    #                     target_payload = next(
    #                         p
    #                         for p in target_payloads
    #                         if p.sequence_index == original_seq
    #                     )
    #                     registrations.append((target_payload, msg))

    #         with db_proxy.atomic():
    #             for payload, msg in registrations:
    #                 RemotePayload.register_upload(
    #                     payload=payload, tg_message=msg, owner=self.current_user
    #                 )
    #             job.set_uploaded()

    #         console.print(
    #             f"[bold green][OK] Smart Forward completado con éxito.[/bold green]"
    #         )
    #     except Exception as e:
    #         logger.error(f"Error en reenvío inteligente: {e}. Limpiando destino...")
    #         for batch_ids in forwarded_history:
    #             for sub_batch in batched(batch_ids, 100):
    #                 try:
    #                     self.client.delete_messages(job.chat.id, sub_batch)  # type: ignore
    #                 except:
    #                     pass
    #         raise e

    # def _cleanup_temp_payload(self, path: Path):
    #     """Elimina archivos fragmentados tras una subida exitosa."""
    #     try:
    #         path.unlink(missing_ok=True)
    #         logger.debug(f"Temporal eliminado: {path.name}")
    #     except Exception as e:
    #         logger.warning(f"No se pudo eliminar temporal {path}: {e}")
