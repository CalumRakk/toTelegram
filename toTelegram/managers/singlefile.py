import json
import os
import lzma

from ..constants import EXT_JSON_XZ
from ..functions import is_filename_too_long, attributes_to_json, TemplateSnapshot
from ..telegram import Telegram
from ..types.file import File


class SingleFile:
    telegram = Telegram()

    def __init__(self,
                 file: File,
                 message=None
                 ):
        self.kind = "single-file"
        self.file = file
        self.message = message

    def update(self, remove=False):
        """
        remove: True para eliminar el archivo una vez se sube a telegram.
        Enviar el archivo a telegram y añade el resultado (message) a la propiedad self.message
        """
        caption = self.filename
        filename = self.filename_for_telegram
        if is_filename_too_long(filename):
            filename = self.file.md5sum + os.path.splitext(filename)[1]
        path = self.path
        self.message = self.telegram.update(
            path, caption=caption, filename=filename)
        if remove:
            os.remove(self.path)

    def to_json(self):
        return attributes_to_json(self)

    def create_snapshot(self):
        template = TemplateSnapshot(self)

        dirname = os.path.dirname(self.file.path)
        filename = os.path.basename(self.file.path)
        path = os.path.join(dirname, filename + EXT_JSON_XZ)

        with lzma.open(path, "wt") as f:
            json.dump(template.to_json(), f)

    @property
    def type(self):
        return self.file.type

    @property
    def filename(self):
        return self.file.filename

    @property
    def path(self):
        return self.file.path

    @property
    def filename_for_telegram(self):
        if is_filename_too_long(self.file.path):
            return self.file.filename
        return self.file.md5sum + os.path.splitext(self.file.filename)[1]
