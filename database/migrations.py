import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


class Migration:
    """Класс для управления миграциями."""
    
    MIGRATIONS = [
        {
            "name": "add_status_to_clients",
            "description": "Добавление колонки status в таблицу clients",
            "sql": "ALTER TABLE clients ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'"
        },
        {
            "name": "create_referrals_table",
            "description": "Создание таблицы referrals для отслеживания рефералов",
            "sql": """
                CREATE TABLE IF NOT EXISTS referrals (
                    id SERIAL PRIMARY KEY,
                    referrer_id INTEGER REFERENCES clients(id) NOT NULL,
                    referred_id INTEGER REFERENCES clients(id) NOT NULL,
                    bonus_days INTEGER DEFAULT 7,
                    bonus_applied BOOLEAN DEFAULT FALSE,
                    referred_paid_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
        },
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