import re
import subprocess
import os
from argparse import ArgumentTypeError

from jsonschema import validate, FormatChecker
import filetype

from .constants import FILE_NAME_LENGTH_LIMIT
regex_get_part_of_filepart = re.compile(r'(?<=_)\d+-\d+')
regex_get_filepart_of_string = re.compile("(?<=').*?(?=')")
regex_md5sum = re.compile(r'([a-f0-9]{32})')      

def get_filepart_of_string(string):
    """
    Obtiene el filepart de un string
    """
    match = regex_get_filepart_of_string.search(string)
    if match:
        return match.group()

def get_part_filepart(filepart):
    """
    Obtiene la part de un filepart
    """

    match = regex_get_part_of_filepart.search(str(filepart))
    if match:
        return match.group()

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
        value = regex_md5sum.search(md5sum).group()
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
    Validaor de tipo de argparse
    """
    path = path.replace('"', "").replace("'", "")

    path = fr"{path}"
    if not os.path.isfile(path):
        raise ArgumentTypeError("The file does not exist: {}".format(path))
    file_name_length(path)
    print(os.path.basename(path))
    return os.path.abspath(path)

def check_md5sum(md5sum,response=".null"):
    """
    Validaor de tipo de argparse.
    si response está presente, devuelve en valor de response en caso de error.
    """
    if bool(md5sum)==False or not len(md5sum) == 32:
        if response!=".null":
            return response
        raise ArgumentTypeError("The md5sum must be 32 characters long")
    
    return md5sum

def get_mime(path):
    kind = filetype.guess(path)
    if kind is None:
        print('Cannot guess file type!')
        return "idk"
    return kind.mime
