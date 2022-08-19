import os
from toTelegram import get_file
from toTelegram.telegram import Telegram

telegram= Telegram()

path=""
if os.path.isfile(path):
    file= get_file(path)  
    if file.type=="single-file":
        if not file.has_been_uploaded:
            message= telegram.update(path=path,caption=file.filename)
            file.message= message
            file.save()
            file.create_fileyaml()
    
    elif file.type=="pieces-file":
        if len(file.pieces)==0:
            file.pieces= file.split()
            file.save()
            
        for piece in file.pieces:
            if bool(piece.message)==False:
                piece.message= telegram.update(piece.path)
                file.save()
        file.create_fileyaml()
    
