import os
from typing import List

from .file import File
from .constants import WORKTABLE
from .telegram import Telegram
from .chunk import Chunk

def download_messages(messages: list, telegram:Telegram):
    paths = []
    for message in messages:
        size = message.size
        filepart = message.filepart
        path = os.path.join(WORKTABLE, filepart)
        if os.path.exists(path) and os.path.getsize(path) == size:
            continue
        paths.append(telegram.client.download_media(message, path="path"))
    return paths

def update(path,md5sum,**kwargs):
    file = File(path, md5sum)
    if not file.is_upload_finished:
        if file.exceed_file_size_limit:
            if not file.is_split_finalized: 
                file.save()                
            
            chunks:List[Chunk]=file.chunks
            for chunk in chunks:
                if not chunk.is_online:
                    chunk.update()
        else:
            file.update()

def download(Links: list, **kwargs):
    telegram= Telegram()
    output = kwargs.get("output", "")

    messages = [telegram.get_message(link) for link in Links]

    checked_messages = find_part(messages[0], messages)
    paths = download_messages(checked_messages["good"], telegram=telegram)

    path = os.path.join(output, filename)
    if not os.path.exists(output):
        # hago toda la ruta.
        pass

    with open(path, "wb") as f:
        for absolute_path in paths:
            with open(absolute_path, "rb") as p:
                for chunk in iter(lambda: p.read(1024 * 8), b''):
                    f.write(chunk)
