from celery import Celery

celery_app = Celery(
    'myapp',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/0',
    include=['tasks']
)

celery_app.conf.update(
    task_modules=[
        'tasks',
    ]
)

#from celery_config import celery_app
from aiogram import Bot
from config import TOKEN

API_TOKEN = TOKEN
bot = Bot(token=API_TOKEN)

@celery_app.task(name='tasks.send_telegram_message')
def send_telegram_message(telegram_id, message):
    import asyncio

    async def send_msg():
        try:
            await bot.send_message(telegram_id, message)
            return {"message": "Message sent successfully"}
        except Exception as e:
            return {"error": str(e)}
    
    return asyncio.run(send_msg())


#backend='redis://localhost:6379/0'