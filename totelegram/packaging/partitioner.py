from pathlib import Path
from typing import List, Tuple

import tartape
from tartape.chunker import calculate_segments
from tartape.schemas import EntryState

from totelegram.packaging.schemas import (
    VirtualChunk,
    VirtualFileFragment,
    VirtualTapeCatalog,
    VirtualTapeMember,
)
from totelegram.schemas import SourceType


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

        segments = (
            list(calculate_segments(file_size, max_chunk_size))
            if file_size > 0
            else [(0, 0)]
        )
        total_parts = len(segments)

        chunks = []
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
    def partition_folder(
        path: Path, max_chunk_size: int, exclude_patterns: List[str]
    ) -> Tuple[VirtualTapeCatalog, List[VirtualChunk], List[VirtualTapeMember]]:
        """Empaqueta virtualmente una carpeta usando tartape sin tocar la base de datos."""
        tape = tartape.Tape(path)

        catalog = VirtualTapeCatalog(
            fingerprint=tape.fingerprint,
            total_size=tape.total_size,
            total_files=tape.count_files,
            tartape_version=tartape.__version__,
            created_at=tape.created_at,
            exclude_patterns=str(exclude_patterns),
        )

        # Calcular segmentos de volúmenes directamente sin crear FileObjects en disco
        segments = list(calculate_segments(tape.total_size, max_chunk_size))
        total_parts = len(segments)

        chunks = []
        for idx, (start, end) in enumerate(segments):
            filename, filename_short = build_payload_names_pure(
                path=path,
                source_type=SourceType.FOLDER,
                md5sum=catalog.fingerprint,
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

        # Transmisión streaming de coordenadas GPS en una sola pasada SQL O(1)
        members = []
        for track_gps in tape.iter_gps(tag_or_size=max_chunk_size):
            if track_gps.is_dir:
                continue

            checksum_val = track_gps.checksum
            if checksum_val is None:
                raise ValueError(
                    f"File '{track_gps.arc_path}' has no recorded checksum in catalog"
                )

            total_frags = len(track_gps.fragments)
            frags = []

            for frag_idx, sl in enumerate(track_gps.fragments):
                if total_frags == 1:
                    frag_state = EntryState.COMPLETE.value
                elif frag_idx == 0:
                    frag_state = EntryState.HEAD.value
                elif frag_idx == total_frags - 1:
                    frag_state = EntryState.TAIL.value
                else:
                    frag_state = EntryState.BODY.value

                frags.append(
                    VirtualFileFragment(
                        vol_idx=sl.volume_index,
                        volume_offset=sl.volume_offset,
                        volume_length=sl.volume_length,
                        source_offset=sl.source_offset,
                        state=frag_state,
                    )
                )

            members.append(
                VirtualTapeMember(
                    relative_path=track_gps.arc_path,
                    size=track_gps.size,
                    md5sum=checksum_val,
                    start_offset=track_gps.start_offset,
                    fragments=frags,
                )
            )

        return catalog, chunks, members
