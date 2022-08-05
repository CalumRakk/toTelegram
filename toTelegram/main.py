import os
from typing import List, Union

import yaml

from .file import File
from .constants import WORKTABLE, EXT_YAML, REGEX_MESSAGE_LINK
from .telegram import TELEGRAM
from .chunk import Chunk
from .file_online import File_Online
from .functions import check_of_input


def get_files_online(messagesplus) -> List[File_Online]:
    files = []
    for messageplus in messagesplus:
        file = File_Online(messageplus, messagesplus)
        if not any(file.filename == item.filename for item in files):
            files.append(file)
    return files


def get_messages(links):
    messagesplus = []
    bad_links = []
    for link in links:
        if not REGEX_MESSAGE_LINK.search(link):
            bad_links.append(link)
            continue
        messagesplus.append(TELEGRAM.get_message(link))
    return messagesplus, bad_links


def download_yaml(target: str, output: str):
    with open(target, "r") as f:
        document = yaml.load(f, Loader=yaml.UnsafeLoader)


def update(path, md5sum: str, **kwargs):
    cut=kwargs.get("cut", False)
    path= check_of_input(path,cut=cut)
    for item in path:
        if type(item)==tuple:
            print(item)
            continue
        
        print(os.path.basename(item))
        file = File(item, md5sum)
        if not file.is_upload_finished:
            if file.exceed_file_size_limit:
                if not file.is_split_finalized:
                    file.split()

                chunks: List[Chunk] = file.chunks
                for chunk in chunks:
                    if not chunk.is_online:
                        chunk.update()
                        chunk.remove()
            else:
                file.update()
            file.create_fileyaml()
        else:
            print("File already uploaded")


def download(target: Union[str, list], **kwargs):
    # FIXME: Si paso enlaces desordenados la concatenación sale erronea. Toca organizar file.parts y las rutas que devuelve file.download()
    # TODO: cada parte crea un file , por lo que se itera por partes repetidas.
    output = kwargs.get("output", "")

    if type(target) == str and target.endswith(EXT_YAML):
        # TODO : añadir una funcion para descargar un archivo yml
        return download_yaml(target, output)

    messagesplus, bad_links = get_messages(target)
    incomplete_files = []
    #files = [File_Online(message, messagesplus) for message in messagesplus]
    files = get_files_online(messagesplus)

    for file in files:
        if not file.is_complete:
            incomplete_files.append(file)
            continue
        dst = os.path.join(output, file.filename)
        temp_path = os.path.join(WORKTABLE, file.filename)
        if not os.path.exists(dst):
            paths = file.download()  # descarga tipo split o unsplit.
            if file.type == "split":
                with open(temp_path, "wb") as f:
                    for path in paths:
                        with open(path, "rb") as p:
                            for chunk in iter(lambda: p.read(1024 * 8), b''):
                                f.write(chunk)
                os.rename(temp_path, dst)
        else:
            print("Ya existe el archivo", dst)

    print("\nArchivos incompletos:", len(incomplete_files))
    # TODO : imprimer los incompletos en vez de los que se tiene.
    for index, file in enumerate(incomplete_files):
        print(f"\t{index+1}")
        print(f"\tfilename: {file.file_name}\n" + f"\tlink: {file.link}")
    print("\nLinks incorrectos:", len(bad_links))
