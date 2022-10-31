
import argparse
from toTelegram import update

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers(dest='command')

parser_update = subparsers.add_parser('update')
parser_update.add_argument(
    'path', help="La ubicación del archivo o carpeta para subir")

parser_a = subparsers.add_parser('backup')
parser_a.add_argument(
    'path', help="La ubicación del archivo o carpeta para subir")
parser_a.add_argument(
    '--snapshot', help="La ubicación del archivo o carpeta para subir")

if __name__ == "__main__":
    args = parser.parse_args()
    if args.command == 'update':
        update(args)