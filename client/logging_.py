
import os

import yaml

from .constants import WORKTABLE, PATH_LOGGING_FILE, PATH_LOGGING_TEMPLATE, PATH_LOGGING_SCHEMA
from .functions import get_md5sum, schema_validation


class Logging:
    def __init__(self, path, md5sum) -> None:
        self.path = os.path.abspath(path)
        self.filename = os.path.basename(path)

        self.md5sum = get_md5sum(self.path) if md5sum == None else md5sum
        self.path_logging = os.path.join(self.folder, PATH_LOGGING_FILE)

        self._document = self._load_document()

    def _load_document(self):

        if os.path.exists(self.path_logging):

            with open(PATH_LOGGING_SCHEMA, 'r') as file:
                schema = yaml.safe_load(file)

            with open(self.path_logging, 'r') as file:
                document = yaml.safe_load(file)

            return schema_validation(schema, document)
        else:
            with open(PATH_LOGGING_TEMPLATE, 'r') as file:
                return yaml.safe_load(file)

    def _save_document(self):
        with open(self.path_logging, 'w') as file:
            yaml.dump(self._document, file)
    @property
    def folder(self):
        path= os.path.join(WORKTABLE, self.md5sum)
        if not os.path.exists(path):
            os.mkdir(path)
        return path
    @property
    def is_complete_split(self):
        return self._document["split_files"]["is_complete"]

    @property
    def is_complete_compressed(self):
        return self._document["compressed_files"]["is_complete"]

    @property
    def is_complete_uploaded(self):
        return self._document["uploaded_files"]["is_complete"]

    @is_complete_split.setter
    def is_complete_split(self, value):
        self._document["split_files"]["is_complete"] = value
        self._save_document()

    @is_complete_compressed.setter
    def is_complete_compressed(self, value):
        self._document["compressed_files"]["is_complete"] = value
        self._save_document()

    @is_complete_uploaded.setter
    def is_complete_uploaded(self, value):
        self._document["uploaded_files"]["is_complete"] = value
        self._save_document()

    @property
    def split_files(self) -> list:
        return self._document["split_files"]["files"]

    @split_files.setter
    def split_files(self, value: str) -> None:
        self._document["split_files"]["files"].extend(value)
        self._save_document()

    @property
    def compressed_files(self) -> list:
        return self._document["compressed_files"]["files"]

    @compressed_files.setter
    def compressed_files(self, value: str) -> None:
        self._document["compressed_files"]["files"].append(value)
        self._save_document()

    @property
    def uploaded_files(self) -> list:
        return self._document["uploaded_files"]["files"]

    @uploaded_files.setter
    def uploaded_files(self, value: str) -> None:
        self._document["uploaded_files"]["files"].append(value)
        self._save_document()
    def is_file_uploaded(self, file):
        """
        Busca en el file está en la dictLy de uploaded_files
        """
        for mydict in self.uploaded_files:
            if mydict["filename"] == file:
                return True
        return False
