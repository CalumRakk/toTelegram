
import unittest
import sys

from toTelegram import update
import toTelegram.constants

sys.path.append('../toTelegram')


FILESIZE_LIMIT = toTelegram.constants.FILESIZE_LIMIT


class Namespace:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, value):
        return None


class TestOptions(unittest.TestCase):
    def test_update_by_piecesfile(self):
        """
        Comprueba la opción update para un archivo tipo piecesfile
        """
        toTelegram.constants.FILESIZE_LIMIT = 1000000
        args = Namespace(path=r"tests\video.mp4", b='c')

        self.assertTrue(update(args))

    def test_update_by_singlefile(self):
        """
        Comprueba la opción update para un archivo tipo singlefile
        """
        toTelegram.constants.FILESIZE_LIMIT = FILESIZE_LIMIT
        args = Namespace(path=r"tests\video.mp4", b='c')
        self.assertTrue(update(args))


if __name__ == '__main__':
    unittest.main()
