from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import config


def main_keyboard() -> InlineKeyboardMarkup:
    """Главное меню клиента."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Попробовать 3 дня бесплатно", callback_data="menu:trial")
    builder.button(text="📊 Статус", callback_data="menu:status")
    builder.button(text="🆘 Помощь / FAQ", callback_data="menu:help")
    builder.button(text="🎁 Пригласить друга", callback_data="menu:invite")
    builder.adjust(1)
    return builder.as_markup()


def tariff_keyboard() -> InlineKeyboardMarkup:
    """Выбор тарифа."""
    builder = InlineKeyboardBuilder()
    for key, tariff in config.TARIFFS.items():
        builder.button(text=f"{tariff['name']} - {tariff['price']} руб.", callback_data=f"tariff:{key}")
    builder.adjust(1)
    return builder.as_markup()


def payment_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура после выбора тарифа."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Я оплатил", callback_data="payment:confirm")
    builder.button(text="📞 Поддержка", url=f"https://t.me/{config.SUPPORT_BOT_USERNAME}")
    builder.adjust(1)
    return builder.as_markup()


def status_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура в статусе."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Продлить подписку", callback_data="menu:tariffs")
    builder.button(text="📞 Поддержка", url=f"https://t.me/{config.SUPPORT_BOT_USERNAME}")
    builder.adjust(1)
    return builder.as_markup()


def help_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура помощи."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Скачать приложения", callback_data="help:downloads")
    builder.button(text="📖 Инструкция", callback_data="help:instructions")
    builder.button(text="📞 Связаться с поддержкой", url=f"https://t.me/{config.SUPPORT_BOT_USERNAME}")
    builder.adjust(1)
    return builder.as_markup()


def downloads_keyboard() -> InlineKeyboardMarkup:
    """Ссылки на скачивание."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🖥 Windows", callback_data="download:windows")
    builder.button(text="🍎 macOS", callback_data="download:macos")
    builder.button(text="📱 Android", callback_data="download:android")
    builder.button(text="🍏 iOS", callback_data="download:ios")
    builder.button(text="📺 Android TV", callback_data="download:androidtv")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def referral_keyboard(client_id: int) -> InlineKeyboardMarkup:
    """Клавиатура реферальной программы."""
    ref_link = f"https://t.me/{config.BOT_USERNAME}?start=ref{client_id}"
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Моя ссылка", url=ref_link)
    builder.adjust(1)
    return builder.as_markup()