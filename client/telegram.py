from pyrogram import Client # pip install pyrogram
from pyrogram.types.messages_and_media.message import Message

def progress(current, total):
    print(f"{current * 100 / total:.1f}%")

class Telegram:
    def __init__(self, config):
        self.config= config
        self.is_client_initialized=False
        
    def _start(self):
        USERNAME= self.config["USERNAME"]
        API_HASH = self.config["API_HASH"]
        API_ID = self.config["API_ID"]
        self.CHAT_ID= self.config["CHAT_ID"]
        
        self._client= Client(USERNAME,API_ID,API_HASH)
        self._client.start()
        self.is_client_initialized=True
    
    @property
    def app(self):
        if not self.is_client_initialized:
            self._start()
        return self._client

    def upload_file(self, path)->dict:   
        print("Subiendo archivo... \n\t", path) 
        value:Message= self.app.send_document(self.CHAT_ID, path,progress=progress) 
        
        media_value= value.media.value
        file= getattr(value, media_value)
        filename= file.file_name
        file_id= file.file_id        
        file_uniqueId= file.file_unique_id
        
        message_id= value.id
        return {
            "filename": filename,
            "file_id": file_id,
            "file_uniqueId": file_uniqueId,
            "message_id": message_id,
        }
             