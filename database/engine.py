from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from config import config
import logging

from database.migrations import Migration

logger = logging.getLogger(__name__)

db_url = config.DATABASE_URL
if db_url and db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(db_url, echo=False, pool_size=10, max_overflow=20)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session


async def init_db():
    """Инициализация базы данных с автоматическим добавлением колонок."""
    async with engine.begin() as conn:
        # Создаём таблицы, если их нет (существующие не трогаем)
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Таблицы созданы (если отсутствовали)")
        
        # Применяем миграции (добавляем недостающие колонки)
        await Migration.apply_all(conn)
        logger.info("База данных инициализирована")