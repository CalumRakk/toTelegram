import io
import math
from pathlib import Path
from typing import List, Union

from totelegram.models import File
from totelegram.setting import Settings


class FileChunker:

    @staticmethod
    def _should_throw_error(file: File, settings: Settings) -> None:
        chunk_size = settings.max_filesize_bytes
        file_size = file.size
        if not file.path.exists():
            raise FileNotFoundError(f"El archivo {file.path} no existe.")
        elif file.category == "single-file":
            raise ValueError("El archivo es single-file, no se puede dividir.")
        elif file_size <= chunk_size:
            raise ValueError(
                f"El tamaño del archivo ({file_size} bytes) es menor/igual que el tamaño de los trozos ({chunk_size} bytes)."
            )

    @staticmethod
    def split_file(
        file: File,
        settings: Settings,
    ) -> List[Path]:
        """Divide un archivo en trozos.
        - `file`: Instancia de File con el archivo a dividir.
        - `settings`: Instancia de Settings con la configuración.

        Devuelve una lista de rutas de los archivos divididos.
        """
        FileChunker._should_throw_error(file, settings)

        # `chunk_size`: Tamaño en bytes de cada trozo.
        chunk_size = settings.max_filesize_bytes
        total_parts, remainder = divmod(file.size, chunk_size)
        if remainder:
            total_parts += 1
        folder = settings.worktable / "chunks"

        # block_size: es el 10% del valor de chunk_size
        # buffer_size: Tamaño del buffer en memoria (por defecto es 100MB).
        block_size = (chunk_size * 10) // 100
        buffer_size = 1024 * 1024 * 100

        chunks_path = []
        with open(file.path, "rb") as f:
            for index, _ in enumerate(range(total_parts), 1):
                chunk_filename = f"{file.path.name}_{index}-{total_parts}"
                chunk_path = folder / chunk_filename
                bytes_written = 0

                with open(chunk_path, "wb") as writer:
                    writer = io.BufferedWriter(writer, buffer_size=buffer_size)  # type: ignore
                    while bytes_written < chunk_size:
                        data = f.read(block_size)
                        if not data:  # EOF
                            break
                        writer.write(data)
                        bytes_written += len(data)
                    writer.flush()
                    writer.close()

                if bytes_written > 0:  # solo agregar si realmente se escribió algo
                    chunks_path.append(chunk_path)
                else:
                    chunk_path.unlink(missing_ok=True)  # elimina archivo vacío

        return chunks_path


if __name__ == "__main__":
    from totelegram.setting import get_settings

    settings = get_settings()
    file = File.get_by_id(1)
    chunks = FileChunker.split_file(file, settings)
