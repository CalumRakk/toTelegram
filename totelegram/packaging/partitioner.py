from pathlib import Path
from typing import List, Tuple

import tartape
from tartape.chunker import calculate_segments

from totelegram.packaging.coordinates import PartCoordinate
from totelegram.packaging.schemas import VirtualChunk
from totelegram.schemas import SourceType


def build_payload_names_pure(
    path: Path,
    source_type: SourceType,
    md5sum: str,
    idx: int,
    total: int,
) -> Tuple[str, str]:
    """Genera nombres técnicos y legibles delegando en PartCoordinate."""
    coord = PartCoordinate(index=idx + 1, total=total)
    part_suffix = coord.filename_suffix

    if source_type == SourceType.FOLDER:
        original_ext = ".tar"
        base_human_name = path.name
        combat_hash = md5sum[:32]
    else:
        original_ext = path.suffix
        base_human_name = path.name
        if original_ext:
            base_human_name = base_human_name[: -len(original_ext)]
        combat_hash = md5sum[:32]

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

        segments = (
            list(calculate_segments(file_size, max_chunk_size))
            if file_size > 0
            else [(0, 0)]
        )
        total_parts = len(segments)

        chunks: List[VirtualChunk] = []
        for idx, (start, end) in enumerate(segments):
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
    def partition_folder(path: Path, max_chunk_size: int) -> List[VirtualChunk]:
        """Calcula las particiones lógicas para los volúmenes TAR de una carpeta."""
        catalog_path = tartape.discover(path)
        tape = (
            tartape.Tape(path, catalog_path=catalog_path)
            if catalog_path
            else tartape.Tape(path)
        )

        segments = list(calculate_segments(tape.total_size, max_chunk_size))
        total_parts = len(segments)

        chunks: List[VirtualChunk] = []
        for idx, (start, end) in enumerate(segments):
            filename, filename_short = build_payload_names_pure(
                path=path,
                source_type=SourceType.FOLDER,
                md5sum=tape.fingerprint,
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
