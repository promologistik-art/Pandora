import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


class Migration:
    """Класс для управления миграциями."""
    
    # Список всех миграций
    MIGRATIONS = [
        {
            "name": "add_status_to_clients",
            "description": "Добавление колонки status в таблицу clients",
            "sql": "ALTER TABLE clients ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'"
        },
        # Добавляйте новые миграции сюда при необходимости
        # {
        #     "name": "add_phone_to_clients",
        #     "description": "Добавление колонки phone в таблицу clients",
        #     "sql": "ALTER TABLE clients ADD COLUMN IF NOT EXISTS phone VARCHAR(20)"
        # },
    ]
    
    @classmethod
    async def apply_all(cls, conn):
        """Применяет все миграции."""
        for migration in cls.MIGRATIONS:
            try:
                logger.info(f"Применяем миграцию: {migration['description']}")
                await conn.execute(text(migration["sql"]))
                logger.info(f"✅ {migration['name']} применена")
            except Exception as e:
                logger.warning(f"❌ Ошибка при миграции {migration['name']}: {e}")