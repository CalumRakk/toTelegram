import argparse
import logging

from totelegram.core.setting import get_settings
from totelegram.logging_config import setup_logging
from totelegram.orchestrator import upload


def run_script():

    parser = argparse.ArgumentParser(
        prog="toTelegram",
        description="Sube archivos a Telegram sin importar el tamaño.",
    )

    command = parser.add_subparsers(dest="command", required=True)

    command_update = command.add_parser("update")
    command_update.add_argument(
        "path", help="La ubicación del archivo o carpeta para subir"
    )

    command_update.add_argument("env", help="La ubicación del archivo .env")

    # command_download = command.add_parser("download")
    # command_download.add_argument("path", help="ruta del archivo json.xz")
    # command_download.add_argument("--output", help="ruta de salida")
    # command_download.add_argument(
    #     "--config-path", help="ruta de salida", default="config.yaml"
    # )

    args = parser.parse_args()
    if args.command == "update":
        settings = get_settings(args.env)
        setup_logging(settings.lod_path, logging.INFO)
        for manifest in upload(target=args.path, settings=settings):
            pass
