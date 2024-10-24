
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, status, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from passlib.context import CryptContext
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import redis
from celery import Celery
#from task.celery_config import celery_app 

#from aiogram.utils.executor import start_polling
import logging
import json
#import random
from config import TOKEN

logging.basicConfig(level=logging.INFO)

app = FastAPI()


API_TOKEN = TOKEN

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


#@celery_app.task
#@celery_app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


SECRET_KEY = "your_secret_key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

redis_client = redis.StrictRedis(host='localhost', port=6379, db=0)
# Настройка подключения к Redis


def create_session(username, token, expire_time):
    redis_client.set(f"session:{token}", json.dumps({"username": username}), ex=expire_time)

def get_session(token):
    session = redis_client.get(f"session:{token}")
    if session:
        return json.loads(session)
    return None

def delete_session(token):
    redis_client.delete(f"session:{token}")

# Database setup
SQLALCHEMY_DATABASE_URL = "postgresql://user:password@localhost/chat"
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    telegram_id = Column(String, unique=True, index=True)
    hashed_password = Column(String)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    sender = Column(String, index=True)
    recipient = Column(String, index=True)
    content = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

@app.post("/register")
async def register(request: Request):
    data = await request.json()
    username = data['username']
    telegram_id = data.get('telegram_id', '')
    password = data['password']
    hashed_password = get_password_hash(password)
    db = SessionLocal()
    existing_user = db.query(User).filter(
        (User.username == username) | (User.telegram_id == telegram_id)
    ).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Telegram ID already exists"
        )
        #return {"message": "Username or Telegram ID already exists"}
    
    new_user = User(
        username=username,
        telegram_id=telegram_id,
        hashed_password=hashed_password
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    db.close()

    # Кэширование данных пользователя
    redis_client.set(f"user:{username}", json.dumps({
        "username": username,
        "telegram_id": telegram_id,
        "hashed_password": hashed_password
    }))

    return {"message": "User registered successfully"}

@app.get("/users")
async def get_users():
    db = SessionLocal()
    users = db.query(User).all()
    db.close()
    return users


def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def authenticate_user(username: str, password: str, db):
    db = SessionLocal()
    user = db.query(User).filter(User.username == username).first()
    db.close()
    if user and verify_password(password, user.hashed_password):
        return user
    return None


@app.post("/login")
async def login(request: Request):
    data = await request.json()
    username = data['username']
    password = data['password']
    db = SessionLocal()
    user = await authenticate_user(username, password, db)
    db.close()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    
    create_session(user.username, access_token, int(access_token_expires.total_seconds()))
    
    return {"access_token": access_token, "token_type": "bearer", "success": True}


@app.post("/logout")
async def logout(token: str = Depends(oauth2_scheme)):
    delete_session(token)
    return {"message": "Successfully logged out"}

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    # Проверка сессии в Redis
    session = get_session(token)
    if not session:
        raise credentials_exception
    
    return session["username"]

@app.get("/users/me")
async def read_users_me(current_user: str = Depends(get_current_user)):
    return {"username": current_user}


async def send_telegram_message(telegram_id, message):
    try:
        await bot.send_message(telegram_id, message, parse_mode=ParseMode.MARKDOWN)
        return {"message": "Message sent successfully"}
    except Exception as e:
        return {"error": str(e)}
    

class ConnectionManager:
    def __init__(self):
        self.active_connections = {}
        
    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        self.active_connections[username] = websocket

    def disconnect(self, username: str):
        if username in self.active_connections:
            del self.active_connections[username]

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:#.values():
            await connection.send_text(message)

    async def handle_offline_messages(self, username: str):
        messages = redis_client.lrange(f"offline:{username}", 0, -1)
        for msg in messages:
            await self.send_personal_message(msg.decode("utf-8"), self.active_connections[username])
        redis_client.delete(f"offline:{username}")


manager = ConnectionManager()

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await manager.connect(websocket, username)
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data.encode('utf-8').decode())
            recipient_username = message_data['to']
            db = SessionLocal()
            recipient = db.query(User).filter(User.username == recipient_username).first()

            # Save the message to the database
            new_message = Message(
                sender=username,
                recipient=recipient_username,
                content=message_data['message']
            )
            db.add(new_message)
            db.commit()

            # Retrieve all messages between the two users
            messages = db.query(Message).filter(
                (Message.sender == username) & (Message.recipient == recipient_username) |
                (Message.sender == recipient_username) & (Message.recipient == username)
            ).order_by(Message.timestamp).all()
            

            # Create a list of messages to send back
            all_messages = [{'sender': msg.sender, 'content': msg.content} for msg in messages]

            if recipient:
                if recipient_username in manager.active_connections:
                    await manager.send_personal_message(
                        json.dumps({'messages': all_messages}, ensure_ascii=False),
                        manager.active_connections[recipient_username]
                    )
                else:
                    await send_telegram_message(recipient.telegram_id, f"{username}: {message_data['message']}")
            db.close()
    except WebSocketDisconnect:
        manager.disconnect(username)
        print(f"Client {username} disconnected")

if __name__ == "__main__":
    import uvicorn
    #import asyncio
    
    uvicorn.run(app, host="localhost", port=8000)
    loop = asyncio.get_event_loop()

    def start_telegram_bot():
        Dispatcher.start_polling(dp, skip_updates=True)

    loop.create_task(loop.run_in_executor(None, start_telegram_bot))
    loop.run_forever()
