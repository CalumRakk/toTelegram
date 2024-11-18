import argparse
from toTelegram.script import update, download


def run_script():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Script para subir archivos a telegram sin importar el tamaño.",
    )  # pylint: disable=C0301
    subparse = parser.add_subparsers(dest="command", required=True)

    # COMANDO : UPDATE
    update_parse = subparse.add_parser("update")
    update_parse.add_argument(
        "path", help="La ubicación del archivo o carpeta para subir"
    )

    update_parse.add_argument("--config-path", default="config.yaml")

    # Argumentos opcionales de exclusión.
    update_parse.add_argument("--exclude-words", nargs="+", default=[])
    update_parse.add_argument("--exclude-ext", nargs="+", default=[])
    update_parse.add_argument("--min-size", default=None)
    update_parse.add_argument("--max-size", default=None)

    # COMANDO : DOWNLOAD
    update_parse = subparse.add_parser("download")
    update_parse.add_argument("path", help="ruta del archivo json.xz")
    update_parse.add_argument("--output", help="ruta de salida")

    update_parse.add_argument(
        "--config-path", help="ruta de salida", default="config.yaml"
    )

    args = parser.parse_args()
    if args.command == "update":
        update(args)
    elif args.command == "download":
        download(args.path)
