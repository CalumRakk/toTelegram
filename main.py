
import argparse
from toTelegram import update

parser = argparse.ArgumentParser(prog='main.py', description='Script para subir archivos a telegram sin importar el tamaño.')
subparse = parser.add_subparsers(dest='command', required=True)

# COMANDO : UPDATE
update_parse = subparse.add_parser('update')
update_parse.add_argument(
    'path', help="La ubicación del archivo o carpeta para subir")

# Argumentos opcionales de exclusión.
update_parse.add_argument(
    '--exclude-words', nargs="+", default=[])
update_parse.add_argument(
    '--exclude-ext', nargs="+", default=[])
update_parse.add_argument('--min-size', default=None)
update_parse.add_argument('--max-size',default=None)


if __name__ == "__main__":
    args = parser.parse_args()
    if args.command == 'update':
        update(args)