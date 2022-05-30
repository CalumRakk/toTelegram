from client.constants import FILESIZE
import argparse
from argparse import ArgumentTypeError
import os.path

from client import Client

def filepath(path):
    if not os.path.isfile(path):
        raise ArgumentTypeError("The file does not exist: {}".format(path))
    return path
def check_md5sum(md5sum):
    if not len(md5sum)==32:
        raise ArgumentTypeError("The md5sum must be 32 characters long")
    return md5sum

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers(dest='command')

parser_split = subparsers.add_parser('split',)
parser_split.add_argument('path', help="La ubicación del archivo a dividir.", type=lambda value: filepath(value))
parser_split.add_argument('--md5sum', help="El md5sum del archivo.", type=lambda value: check_md5sum(value))
parser_split.add_argument('--size', help="El tamaño de los trozos en bytes a los que se dividirá el archivo.", type=int)


# # crea el analizador ("parser") para el commando "enlistar"
parser_update = subparsers.add_parser('update')
parser_update.add_argument('path', help="La ubicación del archivo para subir", type=lambda value: filepath(value))
parser_update.add_argument('--md5sum', help="El md5sum del archivo.", type=lambda value: check_md5sum(value))
parser_update.add_argument('--size', help="El tamaño de los trozos en bytes a los que se dividirá el archivo.", type=int)

# # crea el analizador ("parser") para el commando "pack"
# parser_a = subparsers.add_parser('pack')
# # parser_a.add_argument('pack', type=argparse.FileType('r'))
# # # si "pack" esta presente, este argumento opcional es requerido
# parser_a.add_argument('--json', help="el json que contiene la info de los archivos que conforman el archivo que será gifeado", required=True,type=argparse.FileType('r'))


args = parser.parse_args()

command= args.command
kwargs= vars(args)
if command=='split':        
    client = Client(**kwargs)
    client.split(**kwargs)
elif command=="update":
    client = Client(**kwargs)
    client.update(**kwargs)