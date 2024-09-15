import math
import io
from pathlib import Path
import re


class FileChunker:
    def split_file(
        self,
        path: Path,
        output: Path = Path("chunks"),
        chunk_size: int = 2097152000,
        buffer_size=1024 * 1024 * 100,
    ):
        """Divide un archivo en trozos.
        - `path`: Ruta del archivo a dividir.
        - `output`: Ruta de la carpeta donde se guardarán los trozos (por defecto es "chunks").
        - `chunk_size`: Tamaño en bytes de cada trozo (por defecto es 2GB).
        - `buffer_size`: Tamaño del buffer en memoria (por defecto es 100MB).

        Devuelve una lista de rutas de los archivos divididos.
        """

        file_path = Path(path) if isinstance(path, str) else path
        if not file_path.exists():
            raise FileNotFoundError

        file_size = file_path.stat().st_size

        if file_size < chunk_size:
            raise ValueError("Chunk size is larger than file size")

        folder = Path(output) if isinstance(output, str) else output
        total_parts = math.ceil(file_size / chunk_size)
        folder.mkdir(parents=True, exist_ok=True)

        # block_size es el 10% del valor de chunk_size
        block_size = (chunk_size * 10) // 100

        chunks_path = []
        with open(file_path, "rb") as f:
            for file_number in range(1, total_parts + 1):
                chunk_filename = f"{file_path.name}_{file_number}-{total_parts}"
                chunk_path = folder / chunk_filename
                bytes_written = 0

                with open(chunk_path, "wb") as chunk_file:
                    chunk_file = io.BufferedWriter(chunk_file, buffer_size=buffer_size)
                    while bytes_written < chunk_size:
                        data = f.read(block_size)
                        if not data:  # EOF
                            break
                        chunk_file.write(data)
                        bytes_written += len(data)
                    chunk_file.flush()
                    chunk_file.close()

                chunks_path.append(chunk_path)
        return chunks_path

    def concatenate_files(
        self,
        files: list[Path],
        overwrite=False,
        output=None,
        buffer_size=1024 * 1024 * 100,
    ):
        """Concatenar una lista de archivos divididos en un solo archivo.
        - `files`: Lista de rutas de los archivos divididos.
        - `overwrite`: Indica si se debe sobrescribir el archivo de destino si ya existe.
        - `output`: Ruta del archivo de salida. Si no se especifica, se usará la carpeta del primer trozo.
        - `buffer_size`: Tamaño del buffer en memoria (por defecto es 100MB).
        Devuelve la ruta del archivo concatenado.
        """

        def extraer_numero(file: Path):
            match = re.search(r"_(\d+)-(\d+)", file.name)
            if match:
                return int(match.group(1)), int(match.group(2))
            return (0, 0)

        files_sorted = sorted(files, key=extraer_numero)

        file_path = output or files[0].parent / files[0].name.split("_")[0]
        if file_path.exists() and overwrite is False:
            raise FileExistsError

        with open(file_path, "wb") as f:
            for file in files_sorted:
                with open(file, "rb") as chunk_file:
                    data = chunk_file.read(buffer_size)
                    while data:
                        f.write(data)
                        data = chunk_file.read(buffer_size)
        return file_path
