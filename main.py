import argparse

from toTelegram import ToTelegram,filepath, check_md5sum

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers(dest='command')

parser_update = subparsers.add_parser('update')
parser_update.add_argument('path', help="La ubicación del archivo para subir", type=lambda value: filepath(value))
parser_update.add_argument('--md5sum', help="Si --md5sum está presente el script se salga la comprobacion", type=lambda value: check_md5sum(value))

parser_a = subparsers.add_parser('download')
parser_a.add_argument('target', nargs='*')
# parser_a.add_argument('--json', help="", required=True,type=argparse.FileType('r'))


if __name__=="__main__":
    args = parser.parse_args()
    
    telegram= ToTelegram()  
    options= vars(args) 
        
    if options["command"] == 'update':
        telegram.update(**options)
    
    elif options["command"] == 'download':
        telegram.download(target=options["target"])
