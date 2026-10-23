import re
from dataclasses import dataclass
from typing import Optional

from tartape.units import format_size

from totelegram.packaging.coordinates import PartCoordinate

PROTOCOL_VERSION: int = 6

# Tags canónicos del protocolo
TAG_ROOT_CHUNK = "#totelegram_chunk"
TAG_ROOT_MANIFEST = "#totelegram_manifest"
TAG_TYPE_TAPE = "#type_tape"
TAG_TYPE_FILE = "#type_file"

# Expresiones regulares para parsing bidireccional
_CHUNK_PATTERN = re.compile(
    r"^(?P<filename>[^\n]+)\n\n"
    r"Part\s+\[(?P<cur>\d+)/(?P<tot>\d+)\]\s+•\s+(?P<size>[^\n]+)\n\n"
    r".*?#totelegram_chunk\s+#part_(?P<p_cur>\d+)_(?P<p_tot>\d+)\s+#v(?P<version>\d+)\n"
    r"#src_(?P<hash>[a-fA-F0-9]{32})",
    re.DOTALL,
)

_MANIFEST_PATTERN = re.compile(
    r"^(?P<resource_name>[^\n]+)\n\n"
    r"(?P<metric_line>Total:[^\n]+)\n\n"
    r".*?#totelegram_manifest\s+(?P<type>#type_tape|#type_file)\s+#v(?P<version>\d+)\n"
    r"#src_(?P<hash>[a-fA-F0-9]{32})",
    re.DOTALL,
)


def format_version_tag(version: int | str) -> str:
    """
    Sanitiza cualquier representación de versión a un tag mayor entero seguro.
    Ejemplos: 6 -> '#v6', '6.0' -> '#v6', 'v6.1' -> '#v6'.
    """
    clean = str(version).strip().lstrip("vV")
    major = clean.split(".")[0]
    return f"#v{major}"


@dataclass(frozen=True)
class ChunkCaption:
    """
    Contrato formal para el caption de piezas individuales (chunks)
    """

    filename: str
    part_number: int  # 1-based
    total_parts: int
    part_size: int
    source_short_hash: str
    version: int = PROTOCOL_VERSION

    @property
    def coordinate(self) -> PartCoordinate:
        return PartCoordinate(index=self.part_number, total=self.total_parts)

    def render(self) -> str:
        """Genera el texto estandarizado en 3 bloques en inglés técnico."""
        coord = self.coordinate
        size_str = format_size(self.part_size)
        v_tag = format_version_tag(self.version)
        clean_hash = self.source_short_hash[:32].lower()

        return (
            f"{self.filename}\n\n"
            f"Part {coord.display} • {size_str}\n\n"
            f"{TAG_ROOT_CHUNK} {coord.tag} {v_tag}\n"
            f"#src_{clean_hash}"
        )

    @classmethod
    def parse(cls, text: str) -> Optional["ChunkCaption"]:
        """Deserializa un caption de Telegram devolviendo la instancia tipada o None."""
        if not text or TAG_ROOT_CHUNK not in text:
            return None

        match = _CHUNK_PATTERN.search(text.strip())
        if not match:
            return None

        data = match.groupdict()
        return cls(
            filename=data["filename"].strip(),
            part_number=int(data["cur"]),
            total_parts=int(data["tot"]),
            part_size=0,  # El tamaño exacto reside en document.file_size
            source_short_hash=data["hash"][:32].lower(),
            version=int(data["version"]),
        )


@dataclass(frozen=True)
class ManifestCaption:
    """
    Contrato formal para el caption del manifiesto/snapshot (.ttape o .json.xz).
    """

    resource_name: str
    total_size: int
    total_parts: int
    source_short_hash: str
    is_tape: bool
    total_files: int | None = None
    version: int = PROTOCOL_VERSION

    def render(self) -> str:
        """Genera el texto estandarizado del snapshot en inglés técnico."""
        size_str = format_size(self.total_size)
        v_tag = format_version_tag(self.version)
        type_tag = TAG_TYPE_TAPE if self.is_tape else TAG_TYPE_FILE
        clean_hash = self.source_short_hash[:32].lower()
        parts_label = "part" if self.total_parts == 1 else "parts"
        parts_str = f"({self.total_parts} {parts_label})"

        if self.is_tape and self.total_files is not None:
            files_label = "file" if self.total_files == 1 else "files"
            files_str = f"{self.total_files:,} {files_label}"
            metric_line = f"Total: {size_str} • {files_str} {parts_str}"
        else:
            metric_line = f"Total: {size_str} {parts_str}"

        return (
            f"{self.resource_name}\n\n"
            f"{metric_line}\n\n"
            f"{TAG_ROOT_MANIFEST} {type_tag} {v_tag}\n"
            f"#src_{clean_hash}"
        )

    @classmethod
    def parse(cls, text: str) -> Optional["ManifestCaption"]:
        """Deserializa un caption de snapshot confirmando tipo y versión."""
        if not text or TAG_ROOT_MANIFEST not in text:
            return None

        match = _MANIFEST_PATTERN.search(text.strip())
        if not match:
            return None

        data = match.groupdict()
        is_tape = data["type"] == TAG_TYPE_TAPE

        # Intentar extraer total de partes desde la línea métrica
        parts_match = re.search(r"\((\d+)\s+parts?\)", data["metric_line"])
        total_parts = int(parts_match.group(1)) if parts_match else 1

        # Intentar extraer archivos si es cinta
        files_match = re.search(r"([\d,]+)\s+files?", data["metric_line"])
        total_files = (
            int(files_match.group(1).replace(",", "")) if files_match else None
        )

        return cls(
            resource_name=data["resource_name"].strip(),
            total_size=0,
            total_parts=total_parts,
            source_short_hash=data["hash"][:32].lower(),
            is_tape=is_tape,
            total_files=total_files,
            version=int(data["version"]),
        )
