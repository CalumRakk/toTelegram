import os
import lzma
import json
from .types.file import File
from .managers import PiecesFile, SingleFile
from .exclusionManager import ExclusionManager
from .telegram import Telegram
from pathlib import Path


def update(args):
    telegram = Telegram()
    telegram.check_session()
    telegram.check_chat_id()

    exclusionManager = ExclusionManager(
        exclude_words=args.exclude_words,
        exclude_ext=args.exclude_ext,
        min_size=args.min_size,
        max_size=args.max_size,
    )

    paths = exclusionManager.filder(args.path)
    count_path = len(paths)

    for index, path in enumerate(paths, 1):
        print(f"\n{index}/{count_path}", os.path.basename(path))
        file = File.from_path(path)

        if file.type == "pieces-file":
            manager = PiecesFile.from_file(file)
        else:
            manager = SingleFile(file=file, message=None)

        manager.update()
        manager.create_snapshot()
    return True


def download(path: Path):
    if isinstance(path, str):
        path = Path(path)

    telegram = Telegram()
    telegram.check_session()
    telegram.check_chat_id()

    if path.exists():
        with lzma.open(path) as f:
            json_data = json.load(f)

        file = File(**json_data["manager"]["file"])
        json_data["manager"]["file"] = file
        if file.type == "pieces-file":
            manager = PiecesFile.from_json(json_data["manager"])
        else:
            path = path.parent
            manager = SingleFile.from_json(json_data["manager"])

        manager.download(path)
