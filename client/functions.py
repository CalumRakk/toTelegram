from genericpath import exists
import re
import os.path
import subprocess
import subprocess
import os
import re
import pathlib

from jsonschema import validate, FormatChecker
import yaml

from .constants import (FILESIZE, PATH_CONFIG_FILE,
                       PATH_CONFIG_FILE, PATH_CONFIG_SCHEMA, PATH_CONFIG_TEMPLATE)

regex_get_split = re.compile("(?<=').*?(?=')")
regex_md5sum = re.compile(r'([a-f0-9]{32})')


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
        return regex_md5sum.search(md5sum).group()
    raise Exception("[MD5SUM] No se pudo generar el md5sum")


def split_file(path, output, size=FILESIZE, verbose=True) -> list:
    """
    Divide un archivo en partes de tamaño size.
    path: path del archivo a dividir
    output: carpeta donde se guardará los archivos divididos
    size: tamaño de cada parte
    verbose: mostrar mensajes en consola
    """
    verbose = "--verbose" if verbose else ""

    # path y formato de salida de los archivos. Ejemplo, con output='folder/video.mp4_' el archivo será guardado en folder/video.mp4_01,...
    filename = os.path.basename(path)
    name = filename + "_"
    output = os.path.join(output, name)

    string = f'split "{path}" -b {size} -d {verbose} "{output}"'
    completedProcess = subprocess.run(
        string, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if completedProcess.returncode == 1:
        print("[SPLIT] No se pudo dividir el archivo\n",
              completedProcess.stderr.decode())
        exit()
    print(completedProcess.stdout.decode())
    return [os.path.basename(regex_get_split.search(text).group()) for text in completedProcess.stdout.decode().split("\n")[:-1]]


def compress_file(path: str, output: str, quality=-1, verbose=True) -> None:
    """
    Comprime un archivo a ".gz"
    path: path del archivo comprimir
    output: carpeta donde se guardará el archivo comprimido
    """
    verbose = "--verbose" if verbose else ""

    # formatea la salida:
    filename = os.path.basename(path)
    name = filename + ".gz"
    output = os.path.join(output, name)

    # Convierte el string que representa una ruta de windows (barra invertida "\") a una ruta de linux (con barra inclinada "/")
    input_ = pathlib.PurePath(path).as_posix()
    output = pathlib.PurePath(output).as_posix()

    cmd = fr'gzip -k {verbose} {quality} -c "{input_}" > "{output}"'
    completedProcess = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if completedProcess.returncode == 1:
        print(completedProcess.stderr.decode())
        return False
    print(completedProcess.stdout.decode() or completedProcess.stderr.decode())
    os.remove(path)
    return os.path.basename(output)
