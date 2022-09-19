import os
from json.decoder import JSONDecodeError
import json
from pathlib import Path
from typing import Union,Optional

from ..constants import VERSION, FILESIZE_LIMIT, WORKTABLE, EXT_JSON
from ..functions import get_md5sum_by_hashlib

class Multifile:
    def __init__(self,
                 path: Union[Path, str,list],
                 size: Optional[int] = None,
                 md5sum: Optional[str] = None
                 ):
        self.path = str(path) if type(path)==Path else path 
        self.size = size or os.stat(self.path).st_size
        self.md5sum = md5sum or get_md5sum_by_hashlib(self.path)

    def to_json(self):
        document = self.__dict__.copy()
        if type(self.path) != list:
            path = Path(self.path)
            parents = list(path.parent.parts[1:])
            parents.append(path.name)
            document["path"] = parents
        return document
