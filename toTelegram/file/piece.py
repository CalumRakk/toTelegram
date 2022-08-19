
from ..functions import get_part_filepart

class Piece:
    def __init__(self, json_data: dict):
        self.path = json_data["path"]
        self.filename= json_data["filename"]
        self.message= json_data.get("message", None)
    
    @property
    def part(self)->str:
        return get_part_filepart(self.path)
    
    def to_json(self)->dict:
        return {
            "path": self.path,
            "filename": self.filename,
            "message": self.message.to_json() if self.message else None
        }
        

