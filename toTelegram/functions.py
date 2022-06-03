import re
import subprocess
import os
import re
from argparse import ArgumentTypeError
import pickle
from typing import Union, Dict

from jsonschema import validate, FormatChecker
import yaml
import filetype
from pyrogram.types.messages_and_media.message import Message
from pyrogram.types.messages_and_media.document import Document

from .constants import (PATH_CONFIG_FILE, PATH_CONFIG_FILE,
                        PATH_CONFIG_SCHEMA, PATH_CONFIG_TEMPLATE, EXT_PICKLE, WORKTABLE)

regex_get_part_of_filepart = re.compile(r'(?<=_)\d+-\d+')
regex_get_filepart_of_string = re.compile("(?<=').*?(?=')")
regex_md5sum = re.compile(r'([a-f0-9]{32})')


def get_part_filepart(filepart):
    """
    Obtiene el numero de parte de un archivo/filepart
    """

    match = regex_get_part_of_filepart.search(str(filepart))
    if match:
        return match.group()


def create_filedocument(message: Union[Message, dict]) -> dict:
    """filedocument

    es un diccionario con los datos del archivo subido a Telegram:
        { "filename": file_name, "message_id": message_id,"part": part }

    Nota: si file_name de message no tiene parte,se presupone que es archivo completo y part vale None
    """
    # el filanem debe ser obtenido del message, no del fileyaml. Esto es debido a que telegram o la api te pueden cambiar el nombre del archivo.
    
    value = message.media
    if type(value)==str:
        document: Document = getattr(message, value)
    else:        
        document: Document = getattr(message, value.value)
 
    part = get_part_filepart(document.file_name)
    file_name = document.file_name

    message_id = message.message_id if getattr(message, "message_id", None) else message.id
    
    return {"filepart": file_name, "message_id": message_id, "part": part}

# VALIDADORES DE TIPO


def filepath(path):
    """
    Validaor de tipo de argparse
    """
    if not os.path.isfile(path):
        raise ArgumentTypeError("The file does not exist: {}".format(path))
    return os.path.abspath(path)


def check_md5sum(md5sum):
    """
    Validaor de tipo de argparse
    """
    if not len(md5sum) == 32:
        raise ArgumentTypeError("The md5sum must be 32 characters long")
    return md5sum


def check_fileyaml_object(md5sum):
    """
    Comprueba si existe un objeto fileyaml
    """
    filename = md5sum + EXT_PICKLE
    path = os.path.join(WORKTABLE, filename)
    if os.path.exists(path):
        return True
    return False


def load_fileyaml_object(md5sum):
    """
    Carga un objecto fileyaml existente de lo contrario da error.
    """
    filename = md5sum + EXT_PICKLE
    path = os.path.join(WORKTABLE, filename)
    with open(path, 'rb') as file:
        return pickle.load(file)


def get_mime(path):
    kind = filetype.guess(path)
    if kind is None:
        print('Cannot guess file type!')
        return "idk"
    return kind.mime


def load_config():
    if os.path.exists(PATH_CONFIG_FILE):
        with open(PATH_CONFIG_SCHEMA, 'r') as file:
            schema = yaml.load(file, Loader=yaml.FullLoader)

        with open(PATH_CONFIG_FILE) as f:
            config = yaml.safe_load(f)

        return schema_validation(schema, config)
    else:
        with open(PATH_CONFIG_TEMPLATE, 'r') as file:
            TEMPLATE = yaml.safe_load(file)
        with open(PATH_CONFIG_FILE, 'w') as file:
            yaml.dump(TEMPLATE, file, default_flow_style=False)
        print("Es necesario configurar el archivo de configuración")
        exit()


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
    raise Exception("[MD5SUM] No se pudo generar el md5sum")
