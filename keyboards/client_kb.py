from aiogram.types import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

from config import config


def main_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню клиента."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="💳 Попробовать 3 дня бесплатно")
    builder.button(text="📊 Статус")
    builder.button(text="🆘 Помощь / FAQ")
    builder.button(text="🎁 Пригласить друга")
    builder.adjust(1, 2, 1)
    return builder.as_markup(resize_keyboard=True)


def admin_main_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню админа (объединённое)."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="💳 Попробовать 3 дня бесплатно")
    builder.button(text="📊 Статус")
    builder.button(text="🆘 Помощь / FAQ")
    builder.button(text="🎁 Пригласить друга")
    builder.button(text="⚙️ Админка")
    builder.adjust(1, 2, 1, 1)
    return builder.as_markup(resize_keyboard=True)


def admin_keyboard() -> ReplyKeyboardMarkup:
    """Меню админки."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="👥 Клиенты")
    builder.button(text="📊 Статистика")
    builder.button(text="🖥 Сервер")
    builder.button(text="📢 Рассылка")
    builder.button(text="🧹 Очистка подписок")
    builder.button(text="🔙 Выйти из админки")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup(resize_keyboard=True)


def tariff_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, tariff in config.TARIFFS.items():
        builder.button(
            text=f"{tariff['name']} - {tariff['price']} руб.",
            callback_data=f"tariff:{key}"
        )
    builder.adjust(1)
    return builder.as_markup()


def payment_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Я оплатил", callback_data="payment:confirm")
    builder.button(text="📞 Поддержка", url=f"https://t.me/{config.SUPPORT_BOT_USERNAME}")
    builder.adjust(1)
    return builder.as_markup()


def status_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Продлить подписку", callback_data="menu:tariffs")
    builder.button(text="📞 Поддержка", url=f"https://t.me/{config.SUPPORT_BOT_USERNAME}")
    builder.adjust(1)
    return builder.as_markup()


def help_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Скачать приложения", callback_data="help:downloads")
    builder.button(text="📖 Инструкция", callback_data="help:instructions")
    builder.button(text="📞 Связаться с поддержкой", url=f"https://t.me/{config.SUPPORT_BOT_USERNAME}")
    builder.adjust(1)
    return builder.as_markup()


def downloads_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🖥 Windows", callback_data="download:windows")
    builder.button(text="🍎 macOS", callback_data="download:macos")
    builder.button(text="📱 Android", callback_data="download:android")
    builder.button(text="🍏 iOS", callback_data="download:ios")
    builder.button(text="📺 Android TV", callback_data="download:androidtv")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def referral_keyboard(client_id: int) -> InlineKeyboardMarkup:
    ref_link = f"https://t.me/{(config.BOT_TOKEN.split(':')[0])}?start=ref{client_id}"
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Моя ссылка", url=ref_link)
    builder.adjust(1)
    return builder.as_markup()
