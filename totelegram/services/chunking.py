import logging
from pathlib import Path
from typing import List, Tuple, Union

from totelegram.core.enums import Strategy
from totelegram.core.setting import Settings
from totelegram.store.models import Job, Payload

logger = logging.getLogger(__name__)


class FileChunker:
    @classmethod
    def split_file(
        cls, file_path: Union[str, Path], chunk_size: int, output_folder: Path
    ) -> List[Path]:
        """
        Divide un archivo físico en múltiples fragmentos (chunks) guardados en disco.

        Args:
            file_path: Ruta al archivo original.
            chunk_size: Tamaño máximo de cada fragmento en bytes.
            output_folder: Directorio donde se guardarán los fragmentos resultantes.

        Returns:
            List[Path]: Lista con las rutas de los archivos fragmentados creados.

        Raises:
            FileNotFoundError: Si el archivo origen no existe.
            ValueError: Si el archivo es más pequeño que el chunk_size (no requiere división).
        """
        file_path = Path(file_path)
        output_folder.mkdir(exist_ok=True, parents=True)

        if not file_path.exists():
            raise FileNotFoundError(f"El archivo {file_path} no existe.")

        file_size = file_path.stat().st_size
        if file_size <= chunk_size:
            raise ValueError(
                "El archivo es más pequeño que el tamaño del chunk, no requiere división."
            )

        # Calculamos los rangos y procedemos al corte físico
        ranges = cls._chunk_ranges(file_size, chunk_size)
        chunks = cls._split_file(file_path, ranges, output_folder)

        return chunks

    @classmethod
    def _chunk_ranges(cls, file_size: int, chunk_size: int) -> List[Tuple[int, int]]:
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

    @classmethod
    def _split_file(
        cls,
        file_path: Path,
        ranges: List[Tuple[int, int]],
        folder: Path,
        block_size: int = 1024 * 1024,
    ) -> List[Path]:
        """
        Lee el archivo origen y escribe las partes físicas en disco.
        Utiliza lectura por bloques (buffer) para optimizar el uso de memoria RAM,
        evitando cargar chunks gigantes (ej. 2GB) completamente en memoria.

        Referencia:
            https://chatgpt.com/share/68a6ec3b-988c-8012-b334-f0f2a3524f8c

        Args:
            file_path: Ruta del archivo origen.
            ranges: Lista de tuplas con inicio y fin de bytes.
            folder: Carpeta destino.
            block_size: Tamaño del buffer de lectura (default 1MB).

        Returns:
            List[Path]: Lista de rutas de los archivos generados.
        """
        chunks = []
        total_parts = len(ranges)

        with open(file_path, "rb") as f:
            for idx, (start, end) in enumerate(ranges, start=1):
                f.seek(start)
                remaining = end - start

                # Naming convention: nombre_original_1-5, nombre_original_2-5, etc.
                chunk_filename = f"{file_path.name}_{idx}-{total_parts}"
                chunk_path = folder / chunk_filename

                with open(chunk_path, "wb") as out:
                    while remaining > 0:
                        # Leemos lo que sea menor: el buffer estándar o lo que falta del chunk
                        read_size = min(block_size, remaining)
                        data = f.read(read_size)

                        if not data:
                            break

                        out.write(data)
                        remaining -= len(data)

                chunks.append(chunk_path)

        return chunks


class ChunkingService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def process_job(self, job: Job) -> List[Payload]:
        """
        Prepara los Payloads (unidades de subida) para un Job específico.

        - Si Strategy.SINGLE: Crea un Payload único apuntando al archivo original.
        - Si Strategy.CHUNKED: Utiliza FileChunker para dividir el archivo físico y
          crea múltiples Payloads, uno por cada fragmento.

        Args:
            job: El trabajo (Job) a procesar.

        Returns:
            List[Payload]: Lista de payloads listos para ser subidos.
        """
        # Si ya existen payloads en base de datos, no re-procesamos, solo recuperamos.
        if job.payloads.count() > 0:  # type: ignore
            logger.debug(f"Job {job.id} ya tiene payloads generados. Recuperando...")
            return list(job.payloads.order_by(Payload.sequence_index))  # type: ignore

        if job.strategy == Strategy.SINGLE:
            logger.info(f"Job {job.id}: Estrategia SINGLE. Creando payload único.")
            return Payload.create_payloads(job, [job.path])

        elif job.strategy == Strategy.CHUNKED:
            logger.info(f"Job {job.id}: Estrategia CHUNKED. Iniciando división...")

            chunks_folder = self.settings.worktable / "chunks"

            # Delegamos el trabajo sucio de I/O a FileChunker (misma unidad de código)
            chunks_paths = FileChunker.split_file(
                file_path=job.path,
                chunk_size=self.settings.max_filesize_bytes,
                output_folder=chunks_folder,
            )

            payloads = Payload.create_payloads(job, chunks_paths)
            job.set_splitted()
            return payloads

        return []
