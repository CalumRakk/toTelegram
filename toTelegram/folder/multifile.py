import json
import os
from json.decoder import JSONDecodeError
from pathlib import Path
from typing import Optional, Union

from ..constants import EXT_JSON, FILESIZE_LIMIT, VERSION, WORKTABLE
from ..functions import get_md5sum_by_hashlib


class Multifile:
    def __init__(self,
                 path: Union[Path, str,list],
                 size: Optional[int] = None,
                 md5sum: Optional[str] = None
                 ):
        self.path = path 
        self.size = size or os.path.getsize(self.path)
        self.md5sum = md5sum or get_md5sum_by_hashlib(path,mute=True)

    def to_json(self):
        document = self.__dict__.copy()
        if type(self.path) != list:
            path = Path(self.path)
            parents = list(path.parent.parts[1:])
            parents.append(path.name)
            document["path"] = parents
        return document
