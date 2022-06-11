import os.path

from pyrogram import Client  # pip install pyrogram
from pyrogram.types.messages_and_media.message import Message

from .config import Config

class Telegram(Config):
    def __init__(self):
        self.is_client_initialized = False
        super().__init__()
    
    def _start(self):
        client = Client(self.username, self.api_id, self.api_hash)
        client.start()
        return client

    @property
    def client(self) -> Client:
        if not self.is_client_initialized:
            self._client = self._start()
            self.is_client_initialized = True
        return self._client
    
    def update(self, path: str,caption) -> Message:
        # caption= filepart if temp.exceed_file_size_limit else ""
        filename= os.path.basename(path)
            
        message = self.client.send_document(
                chat_id=self.chat_id, 
                document=path, 
                file_name=filename, 
                caption=filename if caption else "", 
                progress=progress)
        return message    
    def get_message(self, link: str) -> Message:
        pass
    
def progress(current, total):
    print(f"\t\t{current * 100 / total:.1f}%", end="\r")

