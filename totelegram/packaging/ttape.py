"""
totelegram/packaging/ttape.py

Motor de empaquetado, lectura e inspección de Contenedores Híbridos (.ttape).
Encapsula internamente un archivo ZIP con dos artefactos complementarios:
  - manifest.json: Capa de red / transporte de Telegram (sin array inventory).
  - catalog.tartape: Base de datos SQLite pura generada por TarTape en T0.
"""

import json
import logging
import os
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import tartape

from totelegram.packaging.schemas import (
    TTAPE_CATALOG_FILENAME,
    TTAPE_EXTENSION,
    TTAPE_MANIFEST_FILENAME,
    TapeContainerInfo,
    UploadManifest,
)

logger = logging.getLogger(__name__)


class TapeContainerError(Exception):
    """Excepción base para errores en el contenedor .ttape."""


class InvalidTapeContainerError(TapeContainerError):
    """Lanzada cuando un archivo .ttape está corrupto o no contiene los artefactos requeridos."""


class TapeContainer:
    """
    Controlador de alto nivel para contenedores híbridos .ttape.
    Proporciona operaciones atómicas de lectura O(1), escritura y acceso relacional.
    """

    @staticmethod
    def build(
        output_path: Path | str,
        manifest: UploadManifest,
        catalog_path: Path | str,
        overwrite: bool = False,
    ) -> Path:
        """
        Construye un contenedor híbrido .ttape empaquetando el manifest.json y catalog.tartape.

        La escritura se realiza de forma atómica mediante un archivo temporal (.tmp)
        para garantizar que un fallo en disco o corte de energía no deje artefactos corruptos.

        Args:
            output_path: Ruta destino del archivo .ttape.
            manifest: Manifiesto de red (UploadManifest) sin inventario JSON.
            catalog_path: Ruta al archivo SQLite catalog.tartape generado por tartape.
            overwrite: Si es True, reemplaza el destino si ya existe.

        Returns:
            Path: Ruta resuelta del contenedor .ttape generado.

        Raises:
            FileExistsError: Si el archivo destino existe y overwrite es False.
            FileNotFoundError: Si el archivo catalog_path no existe en disco.
        """
        dest = Path(output_path).resolve()
        if dest.suffix != TTAPE_EXTENSION:
            dest = dest.with_name(f"{dest.name}{TTAPE_EXTENSION}")

        if dest.exists() and not overwrite:
            raise FileExistsError(
                f"El contenedor destino ya existe: '{dest}'. Usa overwrite=True para reemplazarlo."
            )

        cat_file = Path(catalog_path).resolve()
        if not cat_file.is_file():
            raise FileNotFoundError(
                f"No se encontró el catálogo SQLite de tartape en: '{cat_file}'"
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        temp_dest = dest.parent / f".{dest.name}.tmp_{os.getpid()}"
        temp_dest.unlink(missing_ok=True)

        # Aseguramos que el manifest no contenga el inventario masivo en JSON
        clean_manifest = manifest.model_copy(deep=True)
        if clean_manifest.source.inventory is not None:
            clean_manifest.source.inventory = None

        manifest_bytes = clean_manifest.model_dump_json(indent=2).encode("utf-8")

        try:
            with zipfile.ZipFile(
                temp_dest,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as zf:
                # 1. Capa de red / transporte
                zf.writestr(TTAPE_MANIFEST_FILENAME, manifest_bytes)
                # 2. Capa de datos / SQLite
                zf.write(cat_file, arcname=TTAPE_CATALOG_FILENAME)

            temp_dest.replace(dest)
            logger.debug(f"Contenedor .ttape creado exitosamente en: {dest}")
            return dest

        except Exception as e:
            temp_dest.unlink(missing_ok=True)
            raise TapeContainerError(
                f"Error al construir el contenedor .ttape en '{dest}': {e}"
            ) from e

    @staticmethod
    def is_valid(path: Path | str) -> bool:
        """Comprueba si un archivo es un contenedor .ttape válido y legible."""
        target = Path(path)
        if not target.is_file():
            return False

        try:
            with zipfile.ZipFile(target, mode="r") as zf:
                namelist = zf.namelist()
                return (
                    TTAPE_MANIFEST_FILENAME in namelist
                    and TTAPE_CATALOG_FILENAME in namelist
                )
        except (zipfile.BadZipFile, OSError):
            return False

    @classmethod
    def read_manifest(cls, ttape_path: Path | str) -> UploadManifest:
        """
        Lee exclusivamente el manifest.json del contenedor en milisegundos O(1).
        Aprovecha el Central Directory del formato ZIP sin descomprimir la base de datos SQLite.

        Args:
            ttape_path: Ruta al archivo .ttape.

        Returns:
            UploadManifest: Manifiesto deserializado y validado.

        Raises:
            InvalidTapeContainerError: Si el archivo no es un contenedor válido.
        """
        target = Path(ttape_path)
        if not target.is_file():
            raise FileNotFoundError(f"Archivo .ttape no encontrado: '{target}'")

        try:
            with zipfile.ZipFile(target, mode="r") as zf:
                if TTAPE_MANIFEST_FILENAME not in zf.namelist():
                    raise InvalidTapeContainerError(
                        f"El contenedor '{target.name}' no contiene '{TTAPE_MANIFEST_FILENAME}'."
                    )

                with zf.open(TTAPE_MANIFEST_FILENAME, mode="r") as f:
                    raw_data = json.load(f)
                    return UploadManifest.model_validate(raw_data)

        except zipfile.BadZipFile as e:
            raise InvalidTapeContainerError(
                f"El archivo '{target.name}' no es un archivo ZIP válido: {e}"
            ) from e
        except Exception as e:
            raise InvalidTapeContainerError(
                f"Error al leer el manifiesto de '{target.name}': {e}"
            ) from e

    @classmethod
    def extract_catalog(
        cls, ttape_path: Path | str, output_catalog_path: Path | str
    ) -> Path:
        """
        Extrae exclusivamente la base de datos SQLite catalog.tartape del contenedor.

        Args:
            ttape_path: Ruta al contenedor .ttape.
            output_catalog_path: Ruta destino para el archivo .tartape extraído.

        Returns:
            Path: Ruta del catálogo extraído.
        """
        target = Path(ttape_path)
        dest = Path(output_catalog_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(target, mode="r") as zf:
                if TTAPE_CATALOG_FILENAME not in zf.namelist():
                    raise InvalidTapeContainerError(
                        f"El contenedor '{target.name}' no contiene '{TTAPE_CATALOG_FILENAME}'."
                    )

                with (
                    zf.open(TTAPE_CATALOG_FILENAME, mode="r") as src,
                    open(dest, "wb") as out_f,
                ):
                    shutil.copyfileobj(src, out_f, length=64 * 1024)

            return dest
        except InvalidTapeContainerError:
            dest.unlink(missing_ok=True)
            raise
        except zipfile.BadZipFile as e:
            dest.unlink(missing_ok=True)
            raise InvalidTapeContainerError(
                f"El archivo '{target.name}' no es un archivo ZIP válido: {e}"
            ) from e
        except Exception as e:
            dest.unlink(missing_ok=True)
            raise TapeContainerError(
                f"Error al extraer el catálogo de '{target.name}': {e}"
            ) from e

    @classmethod
    @contextmanager
    def open_catalog(
        cls, ttape_path: Path | str
    ) -> Generator[tartape.Catalog, None, None]:
        """
        Context Manager que extrae el catálogo a un directorio temporal aislado,
        abre una sesión tartape.Catalog en modo solo lectura y realiza limpieza al salir.

        Ideal para consultas B-Tree quirúrgicas instantáneas sin contaminar el disco.
        """
        target = Path(ttape_path)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_cat = Path(temp_dir) / TTAPE_CATALOG_FILENAME
            cls.extract_catalog(target, temp_cat)

            cat = tartape.open_catalog(temp_cat, read_only=True)
            try:
                yield cat
            finally:
                cat.close()

    @classmethod
    def inspect(cls, ttape_path: Path | str) -> TapeContainerInfo:
        """
        Inspecciona el contenedor y recopila estadísticas físicas de compresión y metadatos.
        """
        target = Path(ttape_path)
        manifest = cls.read_manifest(target)

        try:
            with zipfile.ZipFile(target, mode="r") as zf:
                manifest_info = zf.getinfo(TTAPE_MANIFEST_FILENAME)
                catalog_info = zf.getinfo(TTAPE_CATALOG_FILENAME)

                return TapeContainerInfo(
                    path=target,
                    manifest=manifest,
                    manifest_size_bytes=manifest_info.file_size,
                    catalog_compressed_bytes=catalog_info.compress_size,
                    catalog_uncompressed_bytes=catalog_info.file_size,
                    total_container_bytes=target.stat().st_size,
                )
        except Exception as e:
            raise InvalidTapeContainerError(
                f"Error inspeccionando contenedor '{target.name}': {e}"
            ) from e
