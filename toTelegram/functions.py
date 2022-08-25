import subprocess
import os
from argparse import ArgumentTypeError
from typing import Union
import hashlib

import filetype

from .constants import FILE_NAME_LENGTH_LIMIT, REGEX_FILEPART_OF_STRING, REGEX_PART_OF_FILEPART, REGEX_MD5SUM, VERSION, WORKTABLE,FILESIZE_LIMIT

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

def get_md5sum_by_hashlib(path):
    print("GENERANDO MD5SUM")
    hash_md5 = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def get_md5sum(path):
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
    path= fr"{path}"
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
    True si el nombre del archivo es menor a FILE_NAME_LENGTH_LIMIT\n
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


def get_mime(path):
    kind = filetype.guess(path)
    if kind is None:
        print('Cannot guess file type!')
        return "idk"
    return kind.mime
