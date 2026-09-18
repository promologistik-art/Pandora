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
        {
            "name": "drop_xray_uuid_default",
            "description": "Удаление DEFAULT у колонки xray_uuid в таблице subscriptions",
            "sql": "ALTER TABLE subscriptions ALTER COLUMN xray_uuid DROP DEFAULT"
        },
        {
            "name": "drop_xray_uuid_not_null",
            "description": "Удаление NOT NULL у колонки xray_uuid в таблице subscriptions",
            "sql": "ALTER TABLE subscriptions ALTER COLUMN xray_uuid DROP NOT NULL"
        },
        {
            "name": "add_email_to_routers",
            "description": "Добавление колонки email в таблицу routers",
            "sql": "ALTER TABLE routers ADD COLUMN IF NOT EXISTS email VARCHAR(255)"
        },
        {
            "name": "add_firmware_version_to_routers",
            "description": "Добавление колонки firmware_version в таблицу routers",
            "sql": "ALTER TABLE routers ADD COLUMN IF NOT EXISTS firmware_version VARCHAR(20)"
        },
        {
            "name": "add_last_ip_to_routers",
            "description": "Добавление колонки last_ip в таблицу routers",
            "sql": "ALTER TABLE routers ADD COLUMN IF NOT EXISTS last_ip VARCHAR(45)"
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
                if "already exists" in str(e) or "does not exist" in str(e):
                    logger.warning(f"⚠️ {migration['name']} пропущена (уже применена или отсутствует): {e}")
                else:
                    logger.warning(f"❌ Ошибка при миграции {migration['name']}: {e}")