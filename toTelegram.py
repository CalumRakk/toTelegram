import argparse

from toTelegram import update,check_md5sum,filepath

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers(dest='command')

parser_update = subparsers.add_parser('update')
parser_update.add_argument('path', help="La ubicación del archivo para subir", type=lambda value: filepath(value))
parser_update.add_argument('--md5sum', help="Si --md5sum está presente el script se salta la comprobación", type=lambda value: check_md5sum(value))

parser_a = subparsers.add_parser('download')
parser_a.add_argument('target', nargs='*')


if __name__=="__main__":
    args = parser.parse_args()
    
    options= vars(args) 
        
    if options["command"] == 'update':
        update(**options)