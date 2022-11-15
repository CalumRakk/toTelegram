
import os
from .types.file import File
from .managers import (PiecesFile, SingleFile)
from .exclusionManager import ExclusionManager
from .telegram import Telegram


def update(args):
    Telegram.check_session()
    Telegram.check_chat_id()

    exclusionManager = ExclusionManager(exclude_words=args.exclude_words,
                                        exclude_ext=args.exclude_ext,
                                        min_size=args.min_size,
                                        max_size=args.max_size,)

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
