from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def admin_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню админа."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="👥 Клиенты")
    builder.button(text="📊 Статистика")
    builder.button(text="🖥 Сервер")
    builder.button(text="📢 Рассылка")
    builder.button(text="🔙 Выйти из админки")
    builder.adjust(2, 2, 1)
    return builder.as_markup(resize_keyboard=True)


def admin_back_keyboard() -> ReplyKeyboardMarkup:
    """Возврат в админ-меню."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔙 Назад в админку")
    return builder.as_markup(resize_keyboard=True)