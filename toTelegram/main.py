from pathlib import Path
import os

from . import File, Piecesfile, Singlefile, telegram

# TODO: ELIMINAR LA PIEZA DEL PC CUANDO SE SUBA.


def update(path):
    path = Path(path)
    paths = [i for i in path.glob("*.*")] if path.is_dir() else [path]
    for path in paths:
        file = File(path)
        if file.type == "pieces-file":
            piecesfile = Piecesfile(file)
            if not piecesfile.is_finalized:
                if not piecesfile.is_split_finalized:
                    pieces = piecesfile.split()
                    piecesfile.pieces = pieces
                    piecesfile.save()
                for piece in piecesfile.pieces:
                    if piece.message == False:
                        caption = piece.filename
                        filename = piece.filename_for_telegram
                        piece.message = telegram.update(
                            piece.path, caption=caption, filename=filename)
                        os.remove(piece.path)
                        piecesfile.save()
            piecesfile.create_fileyaml(path)
        else:
            singlefile = Singlefile(file)
            if not singlefile.is_finalized:
                filename = singlefile.filename_for_telegram
                caption = singlefile.file.filename
                singlefile.message = telegram.update(
                    path=path, filename=filename, caption=caption)
                singlefile.save()
            singlefile.create_fileyaml(path)
