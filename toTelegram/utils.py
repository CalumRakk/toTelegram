# pylint: disable=C0301
import hashlib
import os
from argparse import ArgumentTypeError
from typing import Union
from datetime import datetime

import exiftool
import filetype

from .constants import (
    FILE_NAME_LENGTH_LIMIT,
    VERSION,
    REGEX_FILEPART_OF_STRING,
    REGEX_PART_OF_FILEPART,
    PYTHON_DATA_TYPES,
)

METADATA_KEY_TO_BE_EXCLUDED = ["format.filename"]


class TemplateSnapshot:
    def __init__(self, manager):
        self.kind = manager.kind
        self.manager = manager
        self.createdTime = datetime.utcnow()
        self.version = VERSION

    def to_json(self):
        return attributes_to_json(self)


def get_size_of_files(files):
    """
    Consigue el tamaño en bytes de una lista de instancias de File
    """
    size = 0
    for file in files:
        size += file.size
    return size


def get_size_of_folder(path):
    """
    Consigue el tamaño en bytes de un directorio.
    """
    folders = get_all_folders_from_directory(path)
    size = 0
    for folder in folders:
        with os.scandir(folder) as itr:
            for entry in itr:
                size += entry.stat().st_size
    return size


def get_all_folders_from_directory(path):
    """
    Devuelve todas las carpetas que están dentro de la carpeta path
    """
    if not os.path.isdir(path):
        raise Exception("No es una carpeta", path)

    res = []
    for dir_path, dir_names, file_names in os.walk(path):
        pahts = [os.path.join(dir_path, dir) for dir in dir_names]
        res.extend(pahts)
    return res


def get_all_files_from_directory(path, ext=None):
    """
    Devuelve todos los archivos de un directorio con su ruta completa.
    """
    if not os.path.isdir(path):
        raise Exception("No es una carpeta", path)
    res = []
    for dir_path, dir_names, file_names in os.walk(path):
        p_list = []
        for filename in file_names:
            if os.path.splitext(filename)[1] == ext or ext is None:
                p_list.append(os.path.join(dir_path, filename))
        res.extend(p_list)
    return res


def attributes_to_json(self):
    """
    Convierte todos los atributos de un objeto en Json.
    Los atributos privados se excluyen.
    Nota: Los atributos que no son un tipo de dato de Python tienen que tener la implementado el método .to_json()
    """
    document = self.__dict__.copy()
    for key, value in list(document.items())[:]:
        if key.startswith("_"):
            document.pop(key)
            continue
        if key == "pieces":
            document[key] = [i.to_json() for i in document[key]]
            continue
        if isinstance(value, datetime):
            document[key] = str(value)
            continue
        if type(value) not in PYTHON_DATA_TYPES:
            document[key] = value.to_json()
            continue
    return document


def create_mimeType(path):
    """
    Devuelve el mimetype de un archivo. eje: 'image/jpg'
    Nota: parece ser más rapido que exiftool
    """
    kind = filetype.guess(path)
    if kind is None:
        print("Cannot guess file type!")
        return "idk"
    return kind.mime


def create_metadata_by_exiftool(path: Union[str, list]):
    """
    path : Si es un path tipo string devuelve un diccionario con los metadatos. Si es una lista de path tipo string devuelve una lista de diccionario con los metadatos.
    """
    if isinstance(path, str):
        with exiftool.ExifToolHelper() as et:
            metadata = et.get_metadata(path)[0]
        # for key in EXCLUDE_FOLLOWING_KEY: metadata.pop(key)
        return metadata

    with exiftool.ExifToolHelper() as et:
        metadata = et.get_metadata(path)
    # for key in EXCLUDE_FOLLOWING_KEY: metadata.pop(key)
    return metadata


def progress(current, total, filename):
    print("\t", filename, f"{current * 100 / total:.1f}%", end="\r")


def get_filepart_of_string(string):
    """
    Obtiene el filepart de un string
    """
    match = REGEX_FILEPART_OF_STRING.search(string)
    if match:
        return match.group()


def get_part_filepart(filepart):
    """
    Obtiene la part de un filepart
    """

    match = REGEX_PART_OF_FILEPART.search(str(filepart))
    if match:
        return match.group()
    return ""


def create_md5sum_by_hashlib(path, mute=True):
    if mute:
        print("\tGENERANDO MD5SUM")
    hash_md5 = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def is_filename_too_long(path) -> Union[str, bool]:
    """
    True si el nombre del archivo pasa el limite de caracteres de telegram para el filename\n
    Tiene en cuenta la extensión del archivo.
    """
    filename = os.path.basename(path)
    if len(filename) > FILE_NAME_LENGTH_LIMIT:
        return True
    return False


def add_num(path):
    num = 1
    while True:
        dirname = os.path.dirname(path)
        filename = os.path.basename(path)
        ext = os.path.splitext(filename)[1]
        name = filename.replace(ext, "")

        new_filename = f"{name} {num}{ext}"
        new_path = os.path.join(dirname, new_filename)
        if not os.path.exists(new_path):
            return new_path
        num += 1


def cut_filename(path):
    folder = os.path.dirname(path)
    filename: str = os.path.basename(path)
    ext = os.path.splitext(filename)[1]

    new_name = filename.replace(ext, "")[:FILE_NAME_LENGTH_LIMIT] + ext
    new_path = os.path.join(folder, new_name)
    try:
        os.rename(path, new_path)
        with open("files_renamed.txt", "a") as f:
            f.write(f"{filename} -> {new_name}\n")
    except FileExistsError:
        new_path = add_num(new_path)
        os.rename(path, new_path)
        with open("files_renamed.txt", "a") as f:
            f.write(f"{filename} -> {new_name}\n")
    return new_path


def check_md5sum(md5sum, response=".null"):
    """
    Validador de tipo.
    si response está presente, devuelve en valor de response en caso de error.
    """
    if bool(md5sum) is False or not len(md5sum) == 32:
        if response != ".null":
            return response
        raise ArgumentTypeError("The md5sum must be 32 characters long")

    return md5sum


class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):

        if cls not in cls._instances:
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]
