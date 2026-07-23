import sys
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(SCRIPT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parent))

import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from database.engine import init_db

from handlers.client import router as client_router
from handlers.admin import router as admin_router
from services.scheduler import start_scheduler

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

    await bot.set_my_commands([
        types.BotCommand(command="start", description="Главное меню"),
        types.BotCommand(command="status", description="Статус подписки"),
        types.BotCommand(command="help", description="Помощь и FAQ"),
        types.BotCommand(command="invite", description="Пригласить друга"),
        types.BotCommand(command="admin", description="Админ-панель"),
    ])

    dp.include_router(admin_router)
    dp.include_router(client_router)

    await start_scheduler(bot)

    logger.info("Бот @PyxisPandorae_bot запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
