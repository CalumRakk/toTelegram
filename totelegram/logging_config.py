import logging
import os
import sys
import warnings


def logger_formatter() -> logging.Formatter:
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%d-%m-%Y %I:%M:%S %p",
    )
    return formatter


def handler_stream(formatter: logging.Formatter, level: int) -> logging.StreamHandler:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    return console_handler


def handler_file(path: str, formatter: logging.Formatter) -> logging.FileHandler:
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    return file_handler


def handler_supervisor_stdout(formatter: logging.Formatter) -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    # handler.addFilter(lambda record: record.levelno < logging.WARNING)
    handler.setFormatter(formatter)
    return handler


def handler_supervisor_stderr(formatter: logging.Formatter) -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(formatter)
    return handler


def setup_logging(path: str, level: int = logging.INFO) -> None:
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
        handler.close()
        
    formatter = logger_formatter()

    running_under_supervisord = any(
        key in os.environ
        for key in [
            "SUPERVISOR_PROCESS_NAME",
            "SUPERVISOR_ENABLED",
            "SUPERVISOR_GROUP_NAME",
        ]
    )

    if running_under_supervisord:
        handlers = [
            handler_supervisor_stdout(formatter),
            handler_supervisor_stderr(formatter),
        ]
    else:
        handlers = [handler_stream(formatter, level), handler_file(path, formatter)]

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=handlers,
    )

    # Silenciar loggers de librerías de terceros
    libraries_to_silence = [
        "urllib3",
        "seleniumwire",
        "selenium",
        "undetected_chromedriver",
        "hpack",
        "peewee",
        "pyrogram",
    ]
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="pyrogram")
    for lib_name in libraries_to_silence:
        logging.getLogger(lib_name).setLevel(logging.CRITICAL)
