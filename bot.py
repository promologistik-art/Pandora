import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from database.engine import init_db

# Хендлеры
from handlers.client import router as client_router
from handlers.admin import router as admin_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    await init_db()
    logger.info("База данных инициализирована")

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Порядок важен: admin первый для /confirm, /admin
    dp.include_router(admin_router)
    dp.include_router(client_router)

    logger.info("Бот @PyxisPandorae_bot запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())