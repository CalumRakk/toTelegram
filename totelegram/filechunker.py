from pathlib import Path
from typing import List, Union

from totelegram.models import File
from totelegram.setting import Settings


class FileChunker:
    @classmethod
    def split_file(
        cls, 
        file_path: Union[str, Path], 
        chunk_size: int, 
        output_folder: Path
    ) -> List[Path]:
        """
        Divide un archivo en trozos.
        """
        file_path = Path(file_path)
        output_folder.mkdir(exist_ok=True, parents=True)
        
        file_size = file_path.stat().st_size
        
        if not file_path.exists():
            raise FileNotFoundError(f"El archivo {file_path} no existe.")
        if file_size <= chunk_size:
            raise ValueError(f"El archivo es más pequeño que el tamaño del chunk.")

        ranges = cls._chunk_ranges(file_size, chunk_size)
        chunks = cls._split_file(file_path, ranges, output_folder)
        return chunks
    

    @classmethod
    def _should_throw_error(cls, file: File, settings: Settings) -> None:
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
    @classmethod
    def _chunk_ranges(cls, file_size: int, chunk_size: int):
        """Devuelve [(start, end_exclusive), ...] con end_exclusive no incluido.
        https://chatgpt.com/share/68a6ec82-8874-8012-9c27-af04127e28b0
        """
        return [(start, min(start + chunk_size, file_size))
                for start in range(0, file_size, chunk_size)]

    @classmethod
    def _split_file(cls, file_path: Union[str, Path], ranges: list[tuple[int, int]], folder:Path,
                            block_size: int = 1024 * 1024)->List[Path]:
        """
        Genera trozos de archivo leyendo en bloques para no cargar en memoria chunks grandes.

        :param file_path: ruta al archivo
        :param ranges: lista de tuplas (inicio, fin)
        :param block_size: tamaño de bloque de lectura (default 1MB)
        :yield: (index_del_chunk, bloque_bytes)

        https://chatgpt.com/share/68a6ec3b-988c-8012-b334-f0f2a3524f8c
        """
        file_path= Path(file_path) if isinstance(file_path, str) else file_path
        folder.mkdir(exist_ok=True, parents=True)

        chunks=[]
        with open(file_path, "rb") as f:
            for idx, (start, end) in enumerate(ranges, start=1):
                f.seek(start)
                remaining = end - start

                chunk_filename = f"{file_path.name}_{idx}-{len(ranges)}"
                chunk_path = folder / chunk_filename     
                with open(chunk_path, "ab") as out:
                    while remaining > 0:                    
                        data = f.read(min(block_size, remaining))
                        if not data:
                            break
                        out.write(data)
                        remaining -= len(data)
                chunks.append(chunk_path)
        return chunks

    
    @classmethod
    def split_file_dummy(
        cls,
        file: File,
        settings: Settings,
    ) -> List[Path]:
        """Divide un archivo en trozos.
        - `file`: Instancia de File con el archivo a dividir.
        - `settings`: Instancia de Settings con la configuración.

        Devuelve una lista de rutas de los archivos divididos.
        """
        cls._should_throw_error(file, settings)        
        max_filesize_bytes = settings.max_filesize_bytes
        folder = settings.worktable / "chunks"
        ranges= cls._chunk_ranges(file.size, max_filesize_bytes)
        chunks= FileChunker._split_file(file.path, ranges, folder)
        return chunks