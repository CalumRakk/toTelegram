import logging
from typing import List, Tuple

import tartape

from totelegram.common.enums import SourceType
from totelegram.manager.models import Job, Payload

logger = logging.getLogger(__name__)


def chunk_ranges(file_size: int, chunk_size: int) -> List[Tuple[int, int]]:
    """
    Genera una lista de tuplas (inicio, fin) para la división del archivo.
    El rango 'fin' es exclusivo (no incluido en la lectura).

    Referencia:
        https://chatgpt.com/share/68a6ec82-8874-8012-9c27-af04127e28b0

    Args:
        file_size: Tamaño total del archivo.
        chunk_size: Tamaño del bloque deseado.

    Returns:
        List[Tuple[int, int]]: Lista de coordenadas [(0, 100), (100, 200), ...].
    """
    return [
        (start, min(start + chunk_size, file_size))
        for start in range(0, file_size, chunk_size)
    ]


class Chunker:
    @classmethod
    def get_or_create(cls, job: Job) -> List[Payload]:
        """Decide la segmentación basándose en el tipo de recurso."""

        if job.payloads.count() > 0:
            logger.debug(f"El Job {job.id} ya tiene payloads. Saltando segmentación.")
            return list(job.payloads.order_by(Payload.sequence_index))

        if job.source.type == SourceType.FOLDER:
            return cls._process_folder_job(job)
        else:
            return cls._process_file_job(job)

    @classmethod
    def _process_file_job(cls, job: Job) -> List[Payload]:
        """
        # TODO: ACTUALIZAR.
        Procesa un Job y devuelve una lista de Payloads listos para ser subidos.

        - Si Strategy.SINGLE: Crea un Payload único apuntando al archivo original.
        - Si Strategy.CHUNKED: Utiliza FileChunker para dividir el archivo físico y
          crea múltiples Payloads, uno por cada fragmento.

        Args:
            job: El trabajo (Job) a procesar.

        Returns:
            List[Payload]: Lista de payloads listos para ser subidos.
        """
        limit = job.config.tg_max_size
        ranges = chunk_ranges(file_size=job.source.size, chunk_size=limit)
        payloads = []
        count_parts = len(ranges)
        # FIX: consulta N+1
        filename = job.path.name
        for idx, (start, end) in enumerate(ranges):
            part_num = idx + 1
            if count_parts > 1:
                filename = f"{filename}_{part_num}-{count_parts}"

            payloads.append(
                Payload(
                    job=job,
                    sequence_index=idx,
                    md5sum=None,
                    size=end - start,
                    start_offset=start,
                    end_offset=end,
                    filename=filename,
                )
            )
        return payloads

    @classmethod
    def _process_folder_job(cls, job: Job):
        logger.info(f"Planificando volúmenes con TarTape para: {job.source.path_str}")

        if not job.source.tape_catalog:
            raise ValueError("El Job necesita un inventario.")

        tape = tartape.open(job.source.path_str)
        limit = job.config.tg_max_size

        payloads = []
        for vol_idx, (volume_stream, _) in enumerate(tape.iter_volumes(size=limit)):
            payload = Payload(
                job=job,
                sequence_index=vol_idx,
            )
            payloads.append(payload)

        return payloads

    # def _commit_payload_success(cls, payload: Payload, tg_message: "Message", md5: str):
    #     """Guarda el MD5 y vincula el mensaje de Telegram con el Payload."""
    #     from totelegram.manager.database import db_proxy

    #     with db_proxy.atomic():
    #         # Guardamos el MD5 calculado durante la subida
    #         payload.md5sum = md5
    #         payload.save(only=[Payload.md5sum])

    #         # Creamos el acceso efectivo (RemotePayload)
    #         RemotePayload.register_upload(
    #             payload=payload, tg_message=tg_message, owner=cls.current_user
    #         )
