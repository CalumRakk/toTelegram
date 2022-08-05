import argparse

from toTelegram import update, check_md5sum, check_of_input, download

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers(dest='command')

parser_update = subparsers.add_parser('update')
parser_update.add_argument(
    'path', help="La ubicación del archivo o carpeta para subir")
parser_update.add_argument(
    '--md5sum', help="Si --md5sum está presente el script se salta la comprobación", type=lambda value: check_md5sum(value))
parser_update.add_argument(
    '--cut',  nargs='?', const=True, default=False ,help="Si está presente recorta los nombre que superán el limite automaticamente en vez de soltar error")

parser_a = subparsers.add_parser('download')
parser_a.add_argument('target', nargs='*')


if __name__ == "__main__":
    args = parser.parse_args()
    options = vars(args)

    if options["command"] == 'update':
        update(**options)
    elif options["command"] == 'download':
        download(**options)
