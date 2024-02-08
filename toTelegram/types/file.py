import os
import json
import ffmpeg

from ..utils import (
    attributes_to_json,
    create_mimeType,
    create_metadata_by_exiftool,
    create_md5sum_by_hashlib,
)  # pylint: disable=C0301
from .. import constants
from ..config import Config


def get_or_create_md5sum(path):
    """
    Consigue el md5sum de un archivo o lo genera.\n
    Cuando genera el md5sum lo guarda en cache.
    """
    config = Config()
    stat_result = os.stat(path)
    inodo_name = str(stat_result.st_dev) + "-" + str(stat_result.st_ino)
    cache_path = os.path.join(config.path_md5sum, inodo_name)

    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="UTF-8") as file:
            return file.read()
    # Guarda y devuelve el md5sum
    md5sum = create_md5sum_by_hashlib(path)
    with open(cache_path, "w") as file:
        file.write(md5sum)
    return md5sum


def get_or_create_metadata(path, mimetype=None, md5sum=None):
    """
    Genera metadatos de un archivo o devuelve los metadatos que esten en cache.
    Parameters:
        path (``str``):
            ruta completa del archivo.
        mimetype (``str``, *optional*):
            mimeType del archivo. Si no se pasa se intenta obtener
        md5sum (``str``, *optional*):
            md5sum del archivo. Si no se pasa se intenta obtener
    """
    config = Config()
    if mimetype == None:
        mimetype = create_mimeType(path)
    if mimetype.split("/")[0] not in ["image", "video"]:
        return {}

    if md5sum == None:
        md5sum = get_or_create_md5sum(path)

    cache_path = os.path.join(config.path_metadata, md5sum)

    if os.path.exists(cache_path):
        with open(cache_path, "r") as f:
            return json.load(f)

    if "image" in mimetype:
        metadata = create_metadata_by_exiftool(path)
        metadata.pop("SourceFile")
        metadata.pop("File:Directory")
    elif "video" in mimetype:
        metadata = ffmpeg.probe(path)
        metadata["format"].pop("filename")

    with open(cache_path, "w") as f:
        json.dump(metadata, f)
    return metadata


class File:
    """File representa un archivo del sistema.
    Parameters:
        filename (str):
            Nombre del archivo incluido su extensión.
        fileExtension (str):
            Extensión del archivo completa, incluso si tiene doble extensión.
        mimeType (str):
            MimeType del archivo, ejemplo, `text/plain`
        md5sum (str):
            Suma de comprobación md5 del archivo.
        size (int):
            Tamaño en bytes del archivo
        metadata (dict):
            Diccionario con los metadatos arbitrarios del archivo.
    """

    def __init__(
        self,
        filename: str,
        fileExtension: str,
        mimeType: str,
        md5sum: str,
        size: int,
        metadata: dict,
        kind=None,
    ):
        self.kind = kind or "file"
        self.filename = filename
        self.fileExtension = fileExtension
        self.mimeType = mimeType
        self.md5sum = md5sum
        self.size = size
        self.metadata = metadata

    def to_json(self):
        return attributes_to_json(self)

    @property
    def path(self):
        """Devuelve la ruta absoluta del archivo.\n
        **advertencia** Solo está disponible si el archivo fue instanciado via .from_path()

        """
        return getattr(self, "_path", None)

    @property
    def type(self):
        """
        Devuelve el tipo de manager que se debe usar con este archivo.
        """
        return "pieces-file" if self.size > constants.FILESIZE_LIMIT else "single-file"

    @classmethod
    def from_path(cls, path: str):
        """
        Crea una instancia de la clase a partir de un archivo del sistema.
        No se requiere pasar más argumentos que el path del archivo. Este método
        hace uso del cache para ahorrarse tener que generar el md5sum nuevamente.
        Args:
            path : ubicación de un archivo existente en el sistema.

        Returns:
            File: una instancia de esta clase
        """
        path = str(path)
        filename = os.path.basename(path)
        fileExtension = os.path.splitext(path)[1]
        mimeType = create_mimeType(path)
        md5sum = get_or_create_md5sum(path)
        size = os.path.getsize(path)
        metadata = get_or_create_metadata(path, mimeType, md5sum)

        file = File(
            filename=filename,
            fileExtension=fileExtension,
            mimeType=mimeType,
            md5sum=md5sum,
            size=size,
            metadata=metadata,
        )
        setattr(file, "_path", path)
        return file
