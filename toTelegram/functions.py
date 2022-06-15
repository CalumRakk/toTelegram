from __future__ import annotations
import subprocess
import os
from argparse import ArgumentTypeError
from typing import TYPE_CHECKING

from jsonschema import validate, FormatChecker
import filetype

from .constants import FILE_NAME_LENGTH_LIMIT, REGEX_FILEPART_OF_STRING, REGEX_PART_OF_FILEPART, REGEX_MD5SUM

if TYPE_CHECKING:
    from .messageplus import Messageplus
    from .telegram import Telegram


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


def schema_validation(schema, document):
    response = {
        "code": 0,
        "msm": ""
    }
    try:
        validate(
            schema=schema,
            instance=document,
        )
        response["msm"] = "the schama is valid"
    except Exception as ex:
        response["code"] = -1
        response["msm"] = str(ex)
        print(response["msm"])
        exit()
    return document


def get_md5sum(path):
    print("[MD5SUM] Generando...")
    #md5sum = str(subprocess.run(["md5sum","--tag", os.path.abspath(path)],capture_output=True).stdout).split("= ")[1].replace("\\n'","")
    if os.path.exists(path):
        string = fr'md5sum "{path}"'
        md5sum = str(subprocess.run(string, capture_output=True).stdout)
        value = REGEX_MD5SUM.search(md5sum).group()
        return value
    raise Exception("[MD5SUM] El archivo no existe")

# VALIDADORES DE TIPO


def file_name_length(path):
    # File name length up to 60 characters, others will be trimmed out
    filename = os.path.basename(path)
    limit = FILE_NAME_LENGTH_LIMIT
    if len(filename) > limit:
        print("Example:", filename[0:limit])
        raise ArgumentTypeError(
            f"The filename is too long - max  {limit} characters")


def filepath(path):
    """
    Validador de tipo.
    """
    path = path.replace('"', "").replace("'", "")

    path = fr"{path}"
    if not os.path.isfile(path):
        raise ArgumentTypeError("The file does not exist: {}".format(path))
    file_name_length(path)
    print(os.path.basename(path))
    return os.path.abspath(path)


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
