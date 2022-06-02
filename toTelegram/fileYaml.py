import os
import pickle

import yaml

from .constants import WORKTABLE, EXT_PICKLE, FILESIZE_LIMIT, PATH_FILEYAML_TEMPLATE, EXT_YAML
from .functions import get_mime


class FileYaml:
    def __init__(self, path, md5sum) -> None:
        # Mutables al cargar el objeto
        self.path = os.path.abspath(path)

        # No se deben modificar.
        self.mime = get_mime(path)
        self.md5sum =  md5sum
        self.size = os.path.getsize(self.path)
        self.is_split_required = self.size > FILESIZE_LIMIT

        # mutables via setters
        # Los siguientes atributos necesitan ser guardados una vez se cambien, por ello están en setter.
        self._split_files = []
        self._uploaded_files = []
        self._all_files_uploaded = False
        self._is_file_upload_complete = False
        self.is_fileyaml_uploaded= False

    def save(self):
        path = os.path.join(WORKTABLE, self.md5sum + EXT_PICKLE)
        with open(path, 'wb') as f:
            pickle.dump(self, f)

    @property
    def filename(self):
        return os.path.basename(self.path)

    @property
    def uploaded_files(self):
        """
        Devuelve la lista de archivos que ya han sido subidos.
        """
        return self._uploaded_files

    def add_uploaded_file(self, value: dict) -> None:
        """
        Añade un objeto a la lista de archivos subidos.
        """
        self._uploaded_files.append(value)
        self.save()

    def check_file_uploaded(self, value: str) -> bool:
        """
        Busca via filename si un archivo ya ha sido subido.
        """
        for file in self._uploaded_files:
            if file["filename"] == value:
                return True
        return False

    @property
    def is_complete_uploaded_files(self)->bool:
        return self._is_file_upload_complete

    @is_complete_uploaded_files.setter
    def is_complete_uploaded_files(self, value: bool):
        self._is_file_upload_complete = value
        self.save()

    @property
    def split_files(self):
        return self._split_files

    @split_files.setter
    def split_files(self, value: list):
        self._split_files = value
        self.save()
    @property
    def is_split_complete(self):
        """
        True si self.split_files tiene archivos, lo que indica también que se completo el split.
        """
        return bool(self.split_files)
    
    def create_fileyaml(self, chat_id)->str:
        """
        Crea un archivo file.yaml
        chat_id: id del chat donde se subieron los archivos.
        """

        self.clear_split_files()

        with open(PATH_FILEYAML_TEMPLATE, 'r') as f:
            template = yaml.safe_load(f)
        template["filename"] = self.filename
        template["md5sum"] = self.md5sum
        template["mime"] = self.mime
        template["chat_id"] = chat_id
        template["is_split_required"] = self.is_split_required
        template["files"] = self.uploaded_files

        filename = os.path.basename(self.path) + EXT_YAML
        path = os.path.join(os.path.dirname(self.path), filename)
        with open(path, 'w') as f:
            f.write(yaml.dump(template))
        return path
    def clear_split_files(self):
        """
        Elimina los archivos split de la carpeta de trabajo.
        """
        if self.is_split_required:
            for file in self.split_files:
                path = os.path.join(WORKTABLE, file)
                if os.path.exists(path):
                    os.remove(path)