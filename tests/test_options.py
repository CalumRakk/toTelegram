import unittest
from toTelegram.script import update
import toTelegram.constants

FILESIZE_LIMIT = toTelegram.constants.FILESIZE_LIMIT


class TestOptions(unittest.TestCase):
    def test_update_by_piecesfile(self):
        """
        Comprueba la opción update para un archivo tipo piecesfile
        """
        toTelegram.constants.FILESIZE_LIMIT = 1000000

        r = update(path=r"tests\Otan Mian Anoixi (Live - Bonus Track)-(240p).mp4")
        self.assertTrue(r)

    def test_update_by_singlefile(self):
        """
        Comprueba la opción update para un archivo tipo singlefile
        """
        toTelegram.constants.FILESIZE_LIMIT = FILESIZE_LIMIT
        r = update(path=r"tests\video.mp4")
        self.assertTrue(r)


if __name__ == "__main__":
    unittest.main()
