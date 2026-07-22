import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # === Основной бот ===
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # === Админы ===
    ADMIN_IDS: list[int] = field(default_factory=lambda: [
        int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
    ])

    # === База данных ===
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # === 3x-ui API ===
    XUI_HOST: str = os.getenv("XUI_HOST", "")
    XUI_INBOUND_ID: int = int(os.getenv("XUI_INBOUND_ID", "1"))
    XUI_API_TOKEN: str = os.getenv("XUI_API_TOKEN", "")

    # === СБП ===
    SBP_PHONE: str = os.getenv("SBP_PHONE", "+79991234567")
    SBP_BANK: str = os.getenv("SBP_BANK", "Сбер")

    # === Внешние ссылки ===
    SUPPORT_BOT_USERNAME: str = os.getenv("SUPPORT_BOT_USERNAME", "silverzen_bot")
    VK_PAGE: str = os.getenv("VK_PAGE", "https://vk.ru/pyxispandorae")

    # === Тарифы ===
    TRIAL_DAYS: int = 3

    TARIFFS: dict = field(default_factory=lambda: {
        "1month":  {"name": "1 месяц",   "price": 300,  "days": 30},
        "3months": {"name": "3 месяца",  "price": 800,  "days": 90},
        "6months": {"name": "6 месяцев", "price": 1500, "days": 180},
        "12months":{"name": "12 месяцев","price": 2400, "days": 365},
    })

    REFERRAL_BONUS_DAYS: int = 7


config = Config()