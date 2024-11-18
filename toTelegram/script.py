import os
import lzma
import json
from .types.file import File
from .managers import PiecesFile, SingleFile
from .exclusionManager import ExclusionManager
from .telegram import Telegram
from pathlib import Path
from .config import Config


def update(
    path,
    config_path="config.yaml",
    exclude_words=None,
    exclude_ext=None,
    min_size=None,
    max_size=None,
):
    config = Config(config_path)
    telegram = Telegram(config=config)
    telegram.check_session()
    telegram.check_chat_id()

    exclusionManager = ExclusionManager(
        exclude_words=exclude_words,
        exclude_ext=exclude_ext,
        min_size=min_size,
        max_size=max_size,
    )

    paths = exclusionManager.filder(path)
    count_path = len(paths)

    for index, path in enumerate(paths, 1):
        print(f"\n{index}/{count_path}", os.path.basename(path))
        file = File.from_path(path)

        if file.type == "pieces-file":
            manager = PiecesFile.from_file(file, telegram=telegram)
        else:
            manager = SingleFile(file=file, message=None, telegram=telegram)

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
