import argparse
from toTelegram import update

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers(dest='command')

parser_update = subparsers.add_parser('update')
parser_update.add_argument(
    'path', help="La ubicación del archivo o carpeta para subir")
parser_a = subparsers.add_parser('download')
parser_a.add_argument('target', nargs='*')


if __name__ == "__main__":
    args = parser.parse_args()
    options = vars(args)
    if options["command"] == 'update':
        update(args.path)