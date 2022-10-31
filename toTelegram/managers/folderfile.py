
import os
import shutil
import tarfile
import shutil
import lzma
from typing import List

from ..constants import EXT_TAR, PATH_BACKUPS, EXT_JSON_XZ, MINIMUM_SIZE_TO_BACKUP
from ..functions import attributes_to_json, get_all_files_from_directory, get_size_of_files, TemplateSnapshot
from ..file import SubFile, File
from .singlefile import SingleFile
from .piecesfile import PiecesFile
from ..telegram import telegram


class Backup:
    @classmethod
    def from_json(cls, json_data):
        files = [SubFile.from_json(doc) for doc in json_data["files"]]
        is_in_telegram = json_data["is_in_telegram"]
        # if json_data["manager"]["kind"] == "pieces-file":
        #     PiecesFile.from_json(json_data["manager"])
        
        # else:
        #     SingleFile(file, message=None)
        #     return Backup(**json_data)

    def __init__(self, manager=None, files=None, is_in_telegram=None):
        self.kind = "backup"
        self.manager = manager
        self.files = files
        self.is_in_telegram = is_in_telegram

    def update(self, remove=False):
        if self.file.type == "single-file":
            path = self.file.path
            caption = self.file.filename
            filename = self.file.filename_for_telegram
            message = telegram.update(
                path, caption=caption, filename=filename)
            if remove:
                os.remove(self.file.path)
            self.file.message = message
        else:
            raise Exception(
                "Solo se puede usar en Backup con file type singlefile")


class FolderFile:
    @classmethod
    def from_path(self, folder_path, snapshot_path=None):
        """ 
        Devuelve una instancia de FolderFile.
        
        Parametros:
            folder_path (``str``):
                path de la ubicacón de la carpeta a la que se le desea hacer backup.

            snapshot_path (``str``):
                path de la ubicación del snapshot de la carpeta que se le desea hacer backup.
                Por defecto busca el archivo en el mismo lugar de la carpeta.

        Aserciones:
        - Un backup no subido (is_in_telegram==False) debe ser instaciado pasandole un manager instaciado via from_file. Para ello se debe encontrar en el pc el archivo backup generado, por defecto ubicado en "/.TEMP/toTelegram/backups/inodo_name/filename" donde inodo_name es el inodo del folder_path y filename es el valor de manager.file.filename
        - Un backup no subido y al que no se le encontro el backup generado en el path predeterminado, se instancia via from_json y se añade a la lista backups_file_found. 
        - La lista backups `backups_file_found` es para que el usuario decida que hacer con esa información. El atributo backups_file_found hay que pensarlo como un atributo dinamico que aparece si se cumple este punto o desaparece si no se cumple. 
        """

        if os.path.exists(snapshot_path):
            with lzma.open("file.xz") as f:
                json_data = f.read()["manager"]
            backups = []
            backups_file_found = [Backup.from_json(
                **doc) for doc in json_data["backups_file_found"]]
            for backup in json_data["backups"]:
                file_filename = backup["manager"]["file"]["filename"]
                file_path = os.path.join(PATH_BACKUPS, file_filename)

                if backup["is_in_telegram"] == False and not os.path.exists(file_path):
                    backups_file_found.append(Backup.from_json(**backup))
                    continue
                if backup["is_in_telegram"] == True:
                    backups.append(Backup.from_json(**backup))
                    continue

                file = File.from_path(file_path)
                if file.type == "pieces-file":
                    manager = PiecesFile.from_file(file)
                else:
                    manager = SingleFile(file=file, message=None)
                files = [SubFile.from_json(**doc)
                         for doc in json_data["files"]]
                ibackup = Backup(
                    manager=manager, is_in_telegram=False, files=files)
                backups.append(ibackup)

            folderfile = FolderFile(folder_path=folder_path, backups=backups)
            if bool(backups_file_found) == True:
                folderfile.backups_file_found = backups_file_found
            return folderfile

        return FolderFile(folder_path=folder_path, backups=None)

    def __init__(self, folder_path: str, backups: List[Backup], snapshot_output=None):
        self.kind = "folder-file"
        self.folder_path = folder_path
        self.filename = os.path.basename(folder_path)
        self.backups = backups
        self._snapshot_output = snapshot_output

    def is_file_in_backeup(self, file: File):
        """
        Comprueba si un una instancia de File está dentro de alguno de los backup subido.
        Nota:
        - Este método recorre todos los backups en busca de archivos para obtener el md5sum y guardarlos en un atributo privado del objeto. Este recorrido ocurre solo una vez
        """
        if getattr(self, "_files_in_backup", True):
            files_in_backup = []
            for backup in self.backups:
                for file in backup.files:
                    files_in_backup.append(file.md5sum)            
            self._files_in_backup = files_in_backup
        if file.md5sum in self._files_in_backup:
            return True
        return False

    def get_files_without_backup(self):
        res = get_all_files_from_directory(self.path)

        files = [SubFile.from_path(path) for path in res]
        files_to_backup = []
        for file in files:
            if not self.is_file_in_backeup(file):
                files_to_backup.appen(file)
        return files_to_backup

    @ property
    def filename_for_file_backup(self):
        # FIXME: LOS NOMBRES QUE SUPEREN EL LIMITE DE NOMBRE DE TELEGRAM SE CORTAN. CORREGIR ESO.
        name = os.path.basename(self.path)
        index = str(len(self.backups))
        filename = name + "_" + index + EXT_TAR
        return filename

    def create_backup(self, files):
        # path= os.path.join(PATH_BACKUPS, self.filename_for_file_backup)
        stat_result = os.stat(self.folder_path)
        inodo_name = str(stat_result.st_dev) + "-" + str(stat_result.st_ino)
        folder = os.path.join(PATH_BACKUPS, inodo_name)
        if not os.path.exists(folder):
            os.makedirs(folder)
        path = os.path.join(folder, self.filename_for_file_backup)

        with tarfile.open(path, "w") as tar:
            for file in files:
                tar.add(file.path)

        file = File.from_path(path)
        manager = PiecesFile(
            file, pieces=[]) if file.type == "pieces-file" else SingleFile(file, message=None)
        is_in_telegram = False
        return Backup(manager=manager, files=files, is_in_telegram=is_in_telegram)

    def to_json(self):
        return attributes_to_json(self)
    
    def create_snapshot(self):
        template= TemplateSnapshot(self)

        if self._snapshot_output==None:
            snapshot_output= os.path.join(self.folder_path, self.filename + EXT_JSON_XZ)
        
        with lzma.open(snapshot_output, "wt") as f:
            f.write(template.to_json())
            
    def update(self):
        """
        Hace backup a una carpeta.
        
        Aserciones:
        - Si self.backups es False se debe hacer un backup a todos los archivos que se encuentren en la carpeta. Al finalizar no es necesario recorrer self.backups porqué no hay nada que recorrer.
        - Si self.backups no está vacio (bool(self.backups)==True) se debe recorrer para buscar si un backup no se ha subido, y al finalizar se debe comprobar si dentro del folder hay suficientes archivos para hacerle un backup.
        """
        if bool(self.backups) == False:
            files = self.get_files_without_backup()
            backup = self.create_backup(files)
            self.backups.append(backup)
            self.create_snapshot()
            shutil.rmtree(os.path.dirname(backup.file.path))
            return None

        for backup in self.backups:
            if not backup.is_in_telegram:
                backup.manager.update()
                self.create_snapshot()
                shutil.rmtree(os.path.dirname(backup.file.path))
        else:
            print("Finally finished!")
            files = self.get_files_without_backup()
            files_lenght = get_size_of_files(files)
            if files_lenght > MINIMUM_SIZE_TO_BACKUP:
                backup = self.create_backup(files)
                self.backups.append(backup)
                self.create_snapshot()
                shutil.rmtree(os.path.dirname(backup.file.path))

        # if os.path.exists(self.path):
        #     source = r"D:\Usuarios\Leo\Escritorio\github Leo\toTelegram\toTelegram\assets\Desktop.txt"
        #     out = os.path.join(self.path, "Desktop.ini")
        #     with open(source, "r") as f:
        #         with open(out, "w") as file:
        #             file.write(f.read())
        #     os.system(f'attrib +s "{self.path}"')
