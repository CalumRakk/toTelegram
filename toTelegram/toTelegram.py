import os
import subprocess
from subprocess import PIPE
from typing import Union

from pyrogram import Client  # pip install pyrogram
from pyrogram.types.messages_and_media.message import Message
from pyrogram.types.messages_and_media.document import Document

from .fileyaml import FileYaml
from .constants import EXT_YAML, WORKTABLE, FILESIZE_LIMIT
from .functions import check_fileyaml_object, load_fileyaml_object, get_md5sum, load_config, regex_get_split




def progress(current, total):
    print(f"\t\t{current * 100 / total:.1f}%",end="\r")

def get_document(message: Union[Message,dict] )->dict:
    # el filanem debe ser obtenido del message, no del fileyaml. Esto es debido a que telegram o la api te pueden cambiar el nombre del archivo.
    if type(message)==Message:
        value= message.media.value
        document: Document= getattr(message,value)
        
        return {"filename": document.file_name, "message_id": message.id}
    elif type(message)==dict:       
        value= message["media"]["value"]
        document: dict= message["value"]
        return {"filename": document["file_name"], "message_id": message["message_id"]}
    print(message,type(message))
    raise Exception("message no es un Message ni un dict")

def split(fileyaml: FileYaml, verbose=True) -> list:
    """
    Divide el archivo en partes si supera el limite de Telegram y devuelve una lista de rutas completas
    """
    print("- splitting:")

    if not fileyaml.is_split_required:
        return [fileyaml.path]

    if fileyaml.is_split_complete:
        return [os.path.join(WORKTABLE,filepath) for filepath in fileyaml.split_files]

    verbose = "--verbose" if verbose else ""

    name = fileyaml.filename + "_"
    output = os.path.join(WORKTABLE, name)

    cmd = f'split "{fileyaml.path}" -b {FILESIZE_LIMIT} -d {verbose} "{output}"'
    completedProcess = subprocess.run(cmd, stdout=PIPE, stderr=PIPE)

    if completedProcess.returncode == 1:
        print(completedProcess.stderr.decode())
        raise

    filespath = [] # lista de rutas completas
    files=[] # lista de nombres de archivos
    for text in completedProcess.stdout.decode().split("\n")[:-1]:
        filepath = regex_get_split.search(text).group()
        filespath.append(filepath)
        files.append(os.path.basename(filepath))

    fileyaml.split_files = files
    return filespath


class ToTelegram:
    def __init__(self):
        self.config = load_config()
        self.is_client_initialized = False
        self.username = self.config.get("USERNAME","me")
        self.api_hash = self.config["API_HASH"]
        self.api_id = self.config["API_ID"]
        self.chat_id = self.config["CHAT_ID"]

    def _start(self):
        client = Client(self.username, self.api_id, self.api_hash)
        client.start()
        return client

    @property
    def client(self) -> Client:
        if not self.is_client_initialized:
            self._client = self._start()
            self.is_client_initialized = True
        return self._client

    def upload_fileyaml(self, fileyaml: FileYaml):
        path = fileyaml.create_fileyaml(self.chat_id)
        caption = f"md5sum: {fileyaml.md5sum}"
        return self.client.send_document(chat_id=self.chat_id, document=path,
                                            file_name=fileyaml.filename+EXT_YAML, caption=caption, progress=progress)
    

    def update(self, path, md5sum=None, **kwargs):
        verbose = kwargs.get("verbose", True)

        md5sum = md5sum if md5sum else get_md5sum(path)
        print("md5sum:", md5sum)
        if check_fileyaml_object(md5sum):
            fileyaml = load_fileyaml_object(md5sum)
        else:
            fileyaml = FileYaml(path, md5sum)

        # Un video puede ser dividido o no, asi que las rutas las devuelve split.
        filespath = split(fileyaml, verbose)
        print("- uploading:")
        if not fileyaml.is_complete_uploaded_files:
            for filepath in filespath:
                filename= os.path.basename(filepath)
                if not fileyaml.check_file_uploaded(filename):
                    print(f"\t {os.path.basename(filename)}")
                    if not fileyaml.is_split_required:
                        message : Union[Message,dict]  = self.client.send_document(
                            self.chat_id, filepath, file_name=filename, caption=filename,progress=progress)
                    else:
                         message : Union[Message,dict]  = self.client.send_document(
                            self.chat_id, filepath, file_name=filename,progress=progress)
                    
                    print(message, type(message))
                    document = get_document(message).update({"filename": filename})
                    fileyaml.add_uploaded_file(document)
            fileyaml.is_complete_uploaded_files = True
        print("- uploading: Done")
        if not fileyaml.is_fileyaml_uploaded:
            self.upload_fileyaml(fileyaml)      
        print("- fileyaml ha sido subido")

    def get_message(self, link)-> Message:
        iD= link.split("/")[-1]
        return self.client.get_messages(self.chat_id, int(iD))
# Cuando es un video dividido no se añade caption.