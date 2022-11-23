
import os

from ..functions import get_or_create_md5sum, attributes_to_json, create_mimeType, get_or_create_metadata # pylint: disable=C0301
from .. import constants


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
    def __init__(self, filename:str,
                 fileExtension:str,
                 mimeType:str,
                 md5sum:str,
                 size:int,
                 metadata:dict):
        self.kind = "file"
        self.filename = filename
        self.fileExtension = fileExtension
        self.mimeType = mimeType
        self.md5sum = md5sum
        self.size = size
        self.metadata = metadata

    # def save(self):
    #     """Guarda en cache el estado actual del objeto"""
    #     path = os.path.join(WORKTABLE, self.md5sum)
    #     with open(path, "w") as f:
    #         json.dump(self.to_json(), f)

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
    def from_json(cls, Json: dict):
        return File(**Json)

    @classmethod
    def from_path(cls, path:str):
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

        file = File(filename=filename,
                    fileExtension=fileExtension,
                    mimeType=mimeType,
                    md5sum=md5sum,
                    size=size,
                    metadata=metadata
                    )
        setattr(file,"_path", path)
        return file
