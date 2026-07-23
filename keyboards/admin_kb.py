from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def admin_main_keyboard() -> InlineKeyboardMarkup:
    """Главное меню для админа (с кнопкой входа в админку)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Попробовать 3 дня бесплатно", callback_data="menu:trial")
    builder.button(text="📊 Статус", callback_data="menu:status")
    builder.button(text="🆘 Помощь / FAQ", callback_data="menu:help")
    builder.button(text="🎁 Пригласить друга", callback_data="menu:invite")
    builder.button(text="⚙️ Админка", callback_data="menu:admin")
    builder.adjust(1)
    return builder.as_markup()


def admin_keyboard() -> InlineKeyboardMarkup:
    """Админ-панель."""
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Клиенты", callback_data="admin:clients")
    builder.button(text="📊 Статистика", callback_data="admin:stats")
    builder.button(text="🖥 Сервер", callback_data="admin:server")
    builder.button(text="📢 Рассылка", callback_data="admin:broadcast")
    builder.button(text="🧹 Очистка истекших", callback_data="admin:cleanup")
    builder.button(text="🔙 Выйти из админки", callback_data="admin:exit")
    builder.adjust(1)
    return builder.as_markup()