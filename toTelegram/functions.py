
import hashlib
import os
import json
import subprocess
from argparse import ArgumentTypeError
from typing import Union, List
from datetime import datetime

import humanize
import exiftool
import filetype
import ffmpeg

from .constants import (FILE_NAME_LENGTH_LIMIT, PATH_METADATA,
                        REGEX_FILEPART_OF_STRING, REGEX_MD5SUM,
                        REGEX_PART_OF_FILEPART, PYTHON_DATA_TYPES, PATH_MD5SUM)

EXCLUDE_FOLLOWING_KEY = ["SourceFile", "File:FileName", "File:Directory", "File:FileModifyDate",
                         "File:FileAccessDate", "File:FilePermissions", "File:FileSize", "File:ZoneIdentifier"]

def get_size_of_files(files):
    """
    Consigue el tamaño en bytes de una lista de instancias de File
    """
    size=0
    for file in files:
        size+= file.size
    return size        

def get_size_of_folder(path):
    """
    Consigue el tamaño en bytes de un directorio.
    """
    folders= get_all_folders_in_a_directory(path)
    size=0
    for folder in folders:
        with os.scandir(folder) as itr:
            for entry in itr :
                size+=entry.stat().st_size
    # print(humanize.naturalsize(size))
    return size

def get_all_folders_in_a_directory(path):
    res = []
    for (dir_path, dir_names, file_names) in os.walk(path):
        pahts= [os.path.join(dir_path, dir) for dir in dir_names]
        res.extend(pahts)
    return res

def get_all_files_in_directory(path):
    """
    Devuelve todas los archivos de un directorio con su ruta completa.
    """
    res = []
    for (dir_path, dir_names, file_names) in os.walk(path):
        res.extend([os.path.join(dir_path, filename)
                    for filename in file_names])
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
        if key == "pieces":
            document[key]= [i.to_json() for i in document[key]]
        if type(value) not in PYTHON_DATA_TYPES:
            document[key]= value.to_json()
    return document

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
    if mimetype==None:
        mimetype= create_mimeType(path)
    if mimetype.split('/')[0] not in ["image","video"]:
        return {}
    
    if md5sum==None:
        md5sum= get_or_create_md5sum(path)
        
    cache_path= os.path.join(PATH_METADATA, md5sum)
    
    if os.path.exists(cache_path):
        with open(cache_path, 'r') as f:
            json_data= json.load(f)
        if json_data.get("file") and json_data.get["file"].get("metadata"):
            return json_data["file"]["metadata"]   
    
    if "image" in mimetype:
        metadata= create_metadata_by_exiftool(path)
    elif "video" in mimetype:
        metadata= ffmpeg.probe(path)
    
    with open(cache_path, 'w') as f:
        json.dump(metadata,f)
    return metadata


def create_mimeType(path):
    """
    Devuelve el mimetype de un archivo. eje: 'image/jpg'
    Nota: parece ser más rapido que exiftool
    """
    kind = filetype.guess(path)
    if kind is None:
        print('Cannot guess file type!')
        return "idk"
    return kind.mime


def create_metadata_by_exiftool(path: Union[str, list]):
    """
    path : Si es un path tipo string devuelve un diccionario con los metadatos. Si es una lista de path tipo string devuelve una lista de diccionario con los metadatos.
    """
    if type(path) == str:
        with exiftool.ExifToolHelper() as et:
            metadata = et.get_metadata(path)[0]
        # for key in EXCLUDE_FOLLOWING_KEY: metadata.pop(key)            
        return metadata

    with exiftool.ExifToolHelper() as et:
        metadata = et.get_metadata(path)
    # for key in EXCLUDE_FOLLOWING_KEY: metadata.pop(key)  
    return metadata


def get_or_create_md5sum(path):
    """
    Consigue el md5sum de un archivo o lo genera.\n
    Cuando genera el md5sum lo guarda en cache.
    """
    stat_result = os.stat(path)
    inodo_name = str(stat_result.st_dev) + "-" + str(stat_result.st_ino)
    cache_path = os.path.join(PATH_MD5SUM, inodo_name)

    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding="UTF-8") as file:
            return file.read()
    # Guarda y devuelve el md5sum
    md5sum = create_md5sum_by_hashlib(path)
    with open(cache_path, 'w') as file:
        file.write(md5sum)
    return md5sum


def progress(current, total):
    print(f"\t\t{current * 100 / total:.1f}%", end="\r")


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
        print("GENERANDO MD5SUM")
    hash_md5 = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def create_md5sum(path):
    print("el método create_md5sum está en desuso.")
    print("[MD5SUM] Generando...")
    #md5sum = str(subprocess.run(["md5sum","--tag", os.path.abspath(path)],capture_output=True).stdout).split("= ")[1].replace("\\n'","")
    if os.path.exists(path):
        string = fr"{path}"
        args = ['md5sum', '"', string, '"']
        md5sum = str(subprocess.run(args, capture_output=True).stdout)
        value = REGEX_MD5SUM.search(md5sum).group()

        return value
    raise Exception("[MD5SUM] El archivo no existe")

# VALIDADORES DE TIPO


def check_of_input(path: str, cut):
    """
    Valia la longitud del nombre de los archivos.
    Devuelve una lista de path
    """
    path = path.replace('"', "").replace("'", "")
    path = fr"{path}"
    if os.path.exists(path):
        if os.path.isfile(path):
            if not check_file_name_length(path):
                if cut:
                    return [cut_filename(path)]
                raise ArgumentTypeError(
                    f"El nombre del archivo es muy grande - Maximo de caracteres es {FILE_NAME_LENGTH_LIMIT}")
            return [path]

        paths = [os.path.join(path, file) for file in os.listdir(path)]
        for path in paths[:]:
            if not os.path.isfile(path):
                paths.remove(path)
                continue

            if path.endswith(".txt") or path.endswith(".yaml") or path.endswith(".yml"):
                paths.remove(path)
                continue

            if not check_file_name_length(path):
                if cut:
                    paths.append(cut_filename(path))
                paths.remove(path)
        return paths

    raise ArgumentTypeError(f"No existe la ruta: {path}")


def check_file_name_length(path) -> Union[str, bool]:
    """
    True si el nombre del archivo no pasa el limite de caracteres de telegram para el filename\n
    Se incluye la extensión del archivo.
    """
    filename = os.path.basename(path)
    if len(filename) <= FILE_NAME_LENGTH_LIMIT:
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

    new_name = filename.replace(ext, "")[:FILE_NAME_LENGTH_LIMIT]+ext
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
    if bool(md5sum) == False or not len(md5sum) == 32:
        if response != ".null":
            return response
        raise ArgumentTypeError("The md5sum must be 32 characters long")

    return md5sum
