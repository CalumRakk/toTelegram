import os
from json.decoder import JSONDecodeError
import json
from pathlib import Path
from typing import Union

from ..constants import VERSION, FILESIZE_LIMIT, WORKTABLE, EXT_JSON
from ..functions import get_md5sum_by_hashlib


class File:
    def __init__(self, path: Union[Path, str], md5sum=True):
        self.path = str(path)
        self.filename = getattr(path, "name", None) or os.path.basename(path)
        self.suffix = getattr(
            path, "suffix", None) or os.path.splitext(self.filename)[1]
        stat_result = os.stat(path)
        self.inodo = stat_result.st_ino
        self.dev = stat_result.st_dev
        self.inodo_name = str(self.dev) + "-" + str(self.inodo)
        self.size = stat_result.st_size
        self.type = "pieces-file" if self.size > FILESIZE_LIMIT else "single-file"

        json_data = self._load_file()
        self.md5sum = json_data.get('md5sum') or get_md5sum_by_hashlib(
            self.path) if md5sum == True else None
        self.version = json_data.get('version') or VERSION

    def _load_file(self):
        filename = self.inodo_name + EXT_JSON
        path = os.path.join(WORKTABLE, filename)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except JSONDecodeError:
                pass
        return {}

    def to_json(self):
        return self.__dict__.copy()
