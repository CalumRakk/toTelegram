import os
import subprocess
from subprocess import PIPE
import math
from typing import List

import yaml
from yaml.reader import ReaderError

from .constants import WORKTABLE, FILESIZE_LIMIT, EXT_YAML,VERSION
from .functions import get_mime, get_part_filepart, regex_get_filepart_of_string


class Temp:
    def __init__(self, md5document) -> None:
        self.md5document = md5document

    @property
    def filename(self):
        return os.path.basename(self.path)

    @property
    def is_split_required(self):
        return self.md5document["is_split_required"]

    @property
    def count_parts(self):
        return self.md5document["count_parts"]

    @property
    def fileparts(self):
        return self.md5document["fileparts"]

    @property
    def md5sum(self):
        return self.md5document["md5sum"]

    @property
    def filedocuments(self) -> List[dict]:
        return self.md5document["filedocuments"]

    @property
    def is_complete_filedocument(self):
        """
        Comprueba si todos los archivo se subieron.
        y lo hace tomando una fileparth y comprobando que las partes están en el diccionario.
        """
        if len(self.filedocuments) == 0:
            return False

        # la parte 1-2 se compone desde 1 hasta 2. from se refiere a la parte 1, to a la parte 2
        filedocument = self.filedocuments[0]
        part = filedocument["part"]

        if part is None and len(self.filedocuments) > 1:
            raise Exception(
                "Debe haber un solo filedocument para archivos menores a 2gb")

        if part is None:
            return True

        parts = [filedocument["part"] for filedocument in self.filedocuments]

        count_parts = int(self.filedocuments[0]["part"].split("-")[1])
        total_parts = [str(part) + "-" + str(count_parts)
                       for part in range(1, count_parts+1)]

        print(parts)
        print(total_parts)
        return all(part in parts for part in total_parts)

    @property
    def is_complete_split(self) -> bool:
        return bool(self.fileparts)

    ###

    @property
    def path(self):
        return self.md5document["path"]

    @path.setter
    def path(self, value):
        self.md5document["path"] = value
        self.save()

    @property
    def fileparts(self) -> List[str]:
        return self.md5document["fileparts"]

    @fileparts.setter
    def fileparts(self, value: List[str]) -> None:
        self.md5document["fileparts"] = value
        self.save()

    def add_filedocument(self, filedocument) -> None:
        """
        Añade un filedocument a la lista de archivos subidos.
        """
        if self.check_filepart(filedocument["part"]):
            raise Exception("Filepath already exists")

        self.md5document["filedocuments"].append(filedocument)
        self.save()

    def check_filepart(self, filepath: str) -> bool:
        """
        Busca si el filepart está en la lista de archivos subidos.
        """
        part = get_part_filepart(filepath)
        for filedocument in self.filedocuments:
            if filedocument.get("part") == part:
                return True
        return False

    def save(self):
        path = os.path.join(WORKTABLE, self.md5sum + EXT_YAML)
        with open(path, 'w') as f:
            yaml.dump(self.md5document, f)

    def create_fileyaml(self, chat_id):
        fileyaml = {
            "md5sum": self.md5sum,
            "chat_id": chat_id,
            "files": self.filedocuments,
            "version": VERSION
        }
        dirname = os.path.dirname(self.path)
        name = os.path.splitext(self.filename)[0]
        path = os.path.join(dirname, name + EXT_YAML)
        with open(path, 'w') as f:
            yaml.dump(fileyaml, f)
        return path

    def remove_local_filepart(self, filepart):
        """
        Elimina el filepart local.
        """
        filepart_path = os.path.join(WORKTABLE, filepart)
        if os.path.exists(filepart_path):
            os.remove(filepart_path)


def create_md5document(path, md5sum):
    path = os.path.abspath(path)

    # No se deben modificar.
    mime = get_mime(path)
    md5sum = md5sum
    size = os.path.getsize(path)
    is_split_required = size > FILESIZE_LIMIT
    count_parts = math.ceil(size / FILESIZE_LIMIT)

    fileparts = []
    filedocuments = []
    return {
        "path": path,
        "mime": mime,
        "md5sum": md5sum,
        "size": size,
        "is_split_required": is_split_required,
        "count_parts": count_parts,
        "fileparts": fileparts,
        "filedocuments": filedocuments,
    }


def check_md5document(md5sum) -> bool:
    """
    Comprueba si existe el documento md5sum
    """
    filename = md5sum + EXT_YAML
    path = os.path.join(WORKTABLE, filename)
    if os.path.exists(path):
        return True
    return False


def load_md5document(md5sum, **kwargs):
    path = os.path.join(WORKTABLE, md5sum + EXT_YAML)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'rb') as f:
            md5document = yaml.load(f, Loader=yaml.FullLoader)
    except ReaderError:
        print("Error al leer el archivo temporal: " + path)
        print("Intente subir de nuevo el archivo.")
        os.remove(path)
        exit()

    md5document.update(kwargs)
    return md5document


def split(temp: Temp, verbose=True) -> list:
    """
    Divide el archivo en partes si supera el limite de Telegram y devuelve una lista de filepart
    Administra el tema de Split. Decide si es necesario hacer split o no.
    también actualiza hace cambios sobre temp.
    """
    # TODO: volver process asíncrono para que devuelve las partes que va dividiendo.
    if temp.is_complete_split:
        return temp.fileparts

    verbose = "--verbose" if verbose else ""

    name = temp.filename + "_"
    output = os.path.join(WORKTABLE, name)
    # split video.mp4 -b 1000000 -d --verbose --numeric-suffixes=2 --suffix-length=1 --additional-suffix=test video.mp4_
    digits = len(str(temp.count_parts))
    print("[Split]\n", f"{temp.count_parts} partes")
    cmd = f'split "{temp.path}" -b {FILESIZE_LIMIT} -d {verbose} --suffix-length={digits} --numeric-suffixes=1 --additional-suffix=-{temp.count_parts} "{output}"'
    #cmd = f'split "{temp.path}" -b {FILESIZE_LIMIT} -d {verbose} "{output}"'
    completedProcess = subprocess.run(cmd, stdout=PIPE, stderr=PIPE)

    if completedProcess.returncode == 1:
        print(completedProcess.stderr.decode())
        raise

    fileparts = []  # lista de rutas completas
    for text in completedProcess.stdout.decode().split("\n")[:-1]:
        filepart = regex_get_filepart_of_string.search(text).group()
        fileparts.append(os.path.basename(filepart))
    temp.fileparts = fileparts
    return fileparts
