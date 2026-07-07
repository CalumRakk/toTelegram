from pathlib import Path
from typing import List, Tuple

import tartape

from totelegram.packaging.schemas import (
    VirtualChunk,
    VirtualFileFragment,
    VirtualTapeCatalog,
    VirtualTapeMember,
)
from totelegram.schemas import SourceType


def chunk_ranges(file_size: int, chunk_size: int) -> List[Tuple[int, int]]:
    """Genera coordenadas de lectura (inicio, fin) para la división del archivo."""
    return [
        (start, min(start + chunk_size, file_size))
        for start in range(0, file_size, chunk_size)
    ]


def build_payload_names_pure(
    path: Path,
    source_type: SourceType,
    md5sum: str,
    idx: int,
    total: int,
) -> Tuple[str, str]:
    """Genera nombres técnicos y legibles basados únicamente en tipos primitivos."""
    if source_type == SourceType.FOLDER:
        original_ext = ".tar"
        base_human_name = path.name
        combat_hash = md5sum[:40]
    else:
        original_ext = path.suffix
        base_human_name = path.name
        if original_ext:
            base_human_name = base_human_name[: -len(original_ext)]
        combat_hash = md5sum

    if total > 1:
        padding = max(2, len(str(total)))
        part_suffix = f".{str(idx + 1).zfill(padding)}-{str(total).zfill(padding)}"
    else:
        part_suffix = ""

    full_name = f"{base_human_name}{original_ext}{part_suffix}"
    short_name = f"{combat_hash}{original_ext}{part_suffix}"

    return full_name, short_name


class StatelessPartitioner:
    @staticmethod
    def partition_file(
        path: Path, max_chunk_size: int, md5sum: str
    ) -> List[VirtualChunk]:
        """Calcula la partición lógica para un archivo individual."""
        file_size = path.stat().st_size
        ranges = chunk_ranges(file_size=file_size, chunk_size=max_chunk_size)
        total_parts = len(ranges)

        chunks = []
        for idx, (start, end) in enumerate(ranges):
            filename, filename_short = build_payload_names_pure(
                path=path,
                source_type=SourceType.FILE,
                md5sum=md5sum,
                idx=idx,
                total=total_parts,
            )

            chunks.append(
                VirtualChunk(
                    sequence_index=idx,
                    start_offset=start,
                    end_offset=end,
                    size=end - start,
                    filename=filename,
                    filename_short=filename_short,
                )
            )
        return chunks

    @staticmethod
    def partition_folder(
        path: Path, max_chunk_size: int, exclude_patterns: List[str]
    ) -> Tuple[VirtualTapeCatalog, List[VirtualChunk], List[VirtualTapeMember]]:
        """Empaqueta virtualmente una carpeta usando tartape sin tocar la base de datos."""
        from tartape.chunker import TarChunker

        tar_chunker = TarChunker(chunk_size=max_chunk_size)
        vols = list(tar_chunker.iter_volumes(path))
        total_parts = len(vols)

        # Usamos la primera cinta descubierta para armar los metadatos globales
        tape = tartape.Tape(path)

        catalog = VirtualTapeCatalog(
            fingerprint=tape.fingerprint,
            total_size=tape.total_size,
            total_files=tape.count_files,
            tartape_version=tartape.__version__,
            created_at=tape.created_at,
            exclude_patterns=str(exclude_patterns),
        )

        chunks = []
        members_map = {}

        for idx, (vol, manifest) in enumerate(vols):
            filename, filename_short = build_payload_names_pure(
                path=path,
                source_type=SourceType.FOLDER,
                md5sum=catalog.fingerprint,
                idx=idx,
                total=total_parts,
            )

            chunks.append(
                VirtualChunk(
                    sequence_index=manifest.volume_index,
                    start_offset=manifest.start_offset,
                    end_offset=manifest.end_offset,
                    size=manifest.chunk_size,
                    filename=filename,
                    filename_short=filename_short,
                )
            )

            # Mapeamos los archivos internos (miembros) de este fragmento de cinta
            for entry in manifest.entries:
                if entry.info.is_dir:
                    continue

                arc_path = entry.info.arc_path
                fragment = VirtualFileFragment(
                    vol_idx=manifest.volume_index,
                    offset_in_vol=entry.local_window.start,
                    bytes_in_volume=entry.local_window.end,
                    state=entry.state.value,
                )
                if entry.info.md5sum is None:
                    raise ValueError(f"File {arc_path} has no md5sum")

                if arc_path not in members_map:
                    members_map[arc_path] = VirtualTapeMember(
                        relative_path=arc_path,
                        size=entry.info.size,
                        md5sum=entry.info.md5sum,
                        fragments=[],
                    )
                members_map[arc_path].fragments.append(fragment)

        return catalog, chunks, list(members_map.values())
