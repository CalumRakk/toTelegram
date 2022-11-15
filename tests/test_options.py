
import unittest
import sys
sys.path.append('../toTelegram')

import toTelegram.constants
from toTelegram import update

FILESIZE_LIMIT= toTelegram.constants.FILESIZE_LIMIT

class Namespace:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)    
    def __getattr__(self, value):
        return None
    
class TestOptions(unittest.TestCase):
    def test_update_by_singlefile(self):
        """
        Prueba de subida de un archivo singlefile
        """
        toTelegram.constants.FILESIZE_LIMIT= 1000000
        args = Namespace(path=r"tests\video.mp4", b='c')

        self.assertEqual(update(args), True)
    

if __name__ == '__main__':
    unittest.main()