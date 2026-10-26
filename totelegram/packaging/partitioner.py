from pathlib import Path
from typing import List, Tuple

import tartape
from tartape.chunker import calculate_segments

from totelegram.packaging.coordinates import PartCoordinate
from totelegram.packaging.schemas import (
    VirtualChunk,
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
    @staticmethod
    def partition_folder(
        path: Path, max_chunk_size: int, exclude_patterns: List[str]
    ) -> Tuple[VirtualTapeCatalog, List[VirtualChunk], List[VirtualTapeMember]]:
        """Empaqueta virtualmente una carpeta usando tartape sin tocar la base de datos."""
        catalog_path = tartape.discover(path)
        tape = (
            tartape.Tape(path, catalog_path=catalog_path)
            if catalog_path
            else tartape.Tape(path)
        )

        catalog = VirtualTapeCatalog(
            fingerprint=tape.fingerprint,
            total_size=tape.total_size,
            total_files=tape.count_files,
            tartape_version=tartape.__version__,
            created_at=tape.created_at,
            exclude_patterns=str(exclude_patterns),
        )

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

        # Transmisión de miembros de cinta leyendo directamente los tracks (ADR-009)
        members = []
        if catalog_path and Path(catalog_path).is_file():
            with (
                tartape.open_catalog(catalog_path, read_only=True) as cat,
                cat.db_session,
            ):
                # TODO:Hay varias partes en el código que genera un query para leer la db de tartape
                # Analizar si esto rompe la responsabilidad de totelegram al conocer como está estructurada la db de tartape
                cursor = cat.db_session.db.execute_sql(
                    "SELECT arc_path, size, checksum, start_offset FROM track WHERE is_dir = 0"
                )
                for row in cursor.fetchall():
                    members.append(
                        VirtualTapeMember(
                            relative_path=row[0],
                            size=row[1],
                            md5sum=row[2] or "",
                            start_offset=row[3] or 0,
                        )
                    )

        return catalog, chunks, members
