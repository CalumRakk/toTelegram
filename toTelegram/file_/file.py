import os
import json
import subprocess
import math
from json.decoder import JSONDecodeError

from ..constants import EXT_JSON, WORKTABLE, VERSION,FILESIZE_LIMIT,REGEX_FILEPART_OF_STRING, EXT_JSON
from .piece import Piece
from ..telegram.messageplus import Messageplus
from ..functions import get_md5sum_by_hashlib

class File:
    def __init__(self, path, json_data: dict):
        self.inodo = json_data["inodo"]
        self.dev= json_data["dev"]
        self.path= path
        self.filename = json_data["filename"]
        self.size = json_data["size"]
        self.md5sum = json_data["md5sum"]
        self.type = json_data["type"]       
        self.chat_id = json_data.get("chat_id", None)
        self.pieces = [Piece(json_date) for json_date in json_data["pieces"]] if json_data.get("pieces",False) else []
        self.message= Messageplus(json_data["message"]) if json_data.get("message", False) else None
        self.messages= [Messageplus(json_data) for json_data in json_data["messages"]] if json_data.get("messages", False) else None
        self.has_been_uploaded= json_data.get("has_been_uploaded", False)
        self.version = json_data.get("version", VERSION)

    def save(self):
        filename= str(self.dev) + "-" + str(self.inodo) + EXT_JSON
        path= os.path.join(WORKTABLE, filename)
        json_data= self.to_json()
        with open(path, "w") as file:            
            json.dump(json_data, file)
            
    def to_json(self):
        """
        Devuelve el perfil en formato json.
        """
        document= self.__dict__.copy()
        pieces=[]
        for piece in self.pieces:
            pieces.append(piece.to_json())
        document["pieces"]= pieces
 
        # files=[]
        # for file in self.files:
        #     files.append(file.to_json())
        # document["files"]= files       
        return document

    def split(self):
        """
        Divide el archivo en partes si supera el limite de Telegram
        """
        # TODO: volver process asíncrono para que devuelve las partes que va dividiendo.
        print("[Split]...\n")

        name = self.filename + "_"
        output = os.path.join(WORKTABLE, name)
        count_part= math.ceil(self.size / FILESIZE_LIMIT)
        digits = len(str(count_part))

        cmd = f'split "{self.path}" -b {FILESIZE_LIMIT} -d --verbose --suffix-length={digits} --numeric-suffixes=1 --additional-suffix=-{count_part} "{output}"'

        completedProcess = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if completedProcess.returncode == 1:
            print(completedProcess.stderr.decode())
            raise

        fileparts = []  # lista de rutas completas
        for text in completedProcess.stdout.decode().split("\n")[:-1]:
            filepart = REGEX_FILEPART_OF_STRING.search(text).group()
            basename = os.path.basename(filepart)
            print(f"\t{basename}")
            fileparts.append(basename)

        from .piece import Piece
        pieces = []
        for filepart in fileparts:
            path= os.path.join(WORKTABLE, filepart)
            filename= os.path.basename(path)
            json_data={
                "path": path,
                "filename": filename,
                "message": None
            }
            pieces.append(Piece(json_data))
        return pieces
    def create_fileyaml(self):
        
        if self.type=="pieces-file":
            json_data={
            "filename": self.filename,
            "md5sum": self.md5sum,
            "type": self.type,
            "size": self.size,
            "pieces":[piece.message.to_json() for piece in self.pieces],
            "version": self.version
            }

        dirname = os.path.dirname(self.path)
        ext = os.path.splitext(self.path)[1]
        name = self.filename.replace(ext, "") + EXT_YAML
        path = os.path.join(dirname, name)
        with open(path, "w") as file:
            yaml.dump(document, file)


def load_json(stat_result):    
    inodo= stat_result.st_ino    
    dev= stat_result.st_dev
    filename= str(dev) + "-" + str(inodo) + EXT_JSON
    path= os.path.join(WORKTABLE,filename)
    
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except JSONDecodeError:
            pass
    return None

def get_file(path)->File:
    if os.path.isdir(path):
        raise Exception("The path is a directory")
    
    stat_result = os.stat(path)
    json_data = load_json(stat_result)
    if json_data:
        return File(path,json_data)
    
    inodo = stat_result.st_ino
    dev= stat_result.st_dev
    filename = os.path.basename(path)
    size = stat_result.st_size
    md5sum = get_md5sum_by_hashlib(path)
    type = "pieces-file" if size > FILESIZE_LIMIT else "single-file"
    version = VERSION
    
    json_data={
        "inodo": inodo,
        "dev": dev,
        "filename": filename,
        "size": size,
        "md5sum": md5sum,
        "type": type,
        "version": version
    }
    file= File(path, json_data)
    file.save()
    return file
    
