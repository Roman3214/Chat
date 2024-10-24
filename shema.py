from pydantic import BaseModel
from datetime import datetime

class MessageSchema(BaseModel):
    id: int
    sender_id: int
    receiver_id: int
    content: str
    timestamp: datetime

    class Config:
        orm_mode = True
