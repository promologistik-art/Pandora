from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import config


def admin_keyboard() -> InlineKeyboardMarkup:
    """Админ-панель."""
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Клиенты", callback_data="admin:clients")
    builder.button(text="📊 Статистика", callback_data="admin:stats")
    builder.button(text="🖥 Сервер", callback_data="admin:server")
    builder.button(text="📢 Рассылка", callback_data="admin:broadcast")
    builder.button(text="🧹 Очистка истекших", callback_data="admin:cleanup")
    builder.adjust(1)
    return builder.as_markup()


def clients_list_keyboard(clients: list, page: int = 0) -> InlineKeyboardMarkup:
    """Клавиатура со списком клиентов (каждый ID — кнопка)."""
    builder = InlineKeyboardBuilder()
    
    for client in clients:
        # Определяем эмодзи статуса
        if client.get("status") == "banned":
            emoji = "🚫"
        elif client.get("sub_status") == "paid":
            emoji = "✅"
        elif client.get("sub_status") == "trial":
            emoji = "🆓"
        else:
            emoji = "❌"
        
        button_text = f"{emoji} #{client['id']} @{client['username'] or client['first_name']}"
        builder.button(text=button_text, callback_data=f"admin:user:{client['id']}")
    
    # Кнопки пагинации
    builder.button(text="⬅️ Назад", callback_data=f"admin:clients:{page-1}")
    builder.button(text="➡️ Вперед", callback_data=f"admin:clients:{page+1}")
    builder.button(text="🔄 Обновить", callback_data="admin:clients:refresh")
    builder.adjust(1)  # По одному клиенту на строку
    
    return builder.as_markup()


def user_profile_keyboard(client_id: int, has_subscription: bool = False, subscriptions: list = None) -> InlineKeyboardMarkup:
    """Клавиатура профиля пользователя для админа."""
    builder = InlineKeyboardBuilder()
    
    builder.button(text="📅 Продлить подписку", callback_data=f"admin:extend:{client_id}")
    
    # Если есть активные подписки — показываем кнопку удаления
    if has_subscription and subscriptions:
        builder.button(text="❌ Удалить подписку", callback_data=f"admin:delsub_list:{client_id}")
    
    builder.button(text="🧹 Очистить истекшие", callback_data=f"admin:cleansub:{client_id}")
    
    # Проверяем статус клиента для кнопки блокировки
    # (статус будем передавать отдельно)
    builder.button(text="🚫 Заблокировать", callback_data=f"admin:ban:{client_id}")
    builder.button(text="✅ Разблокировать", callback_data=f"admin:unban:{client_id}")
    builder.button(text="🔙 Назад к списку", callback_data="admin:clients:0")
    builder.adjust(1)
    return builder.as_markup()


def subscription_list_keyboard(subscriptions: list, client_id: int) -> InlineKeyboardMarkup:
    """Клавиатура со списком подписок для удаления."""
    builder = InlineKeyboardBuilder()
    
    for sub in subscriptions:
        button_text = f"ID {sub['id']} | до {sub['expires_at']} | {sub['plan']}"
        builder.button(text=button_text, callback_data=f"admin:delsub_confirm:{client_id}:{sub['id']}")
    
    builder.button(text="🔙 Отмена", callback_data=f"admin:user:{client_id}")
    builder.adjust(1)
    return builder.as_markup()


def confirm_keyboard(action: str, client_id: int, extra: str = "") -> InlineKeyboardMarkup:
    """Универсальная клавиатура подтверждения."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=f"admin:confirm:{action}:{client_id}:{extra}")
    builder.button(text="❌ Нет", callback_data=f"admin:cancel:{action}:{client_id}")
    builder.adjust(2)
    return builder.as_markup()


def confirm_extend_keyboard(client_id: int, days: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения продления."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=f"admin:confirm_extend:{client_id}:{days}")
    builder.button(text="❌ Нет", callback_data=f"admin:cancel_extend:{client_id}")
    builder.adjust(2)
    return builder.as_markup()


def payment_confirm_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения платежа."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Ввести сумму", callback_data=f"admin:payment_amount:{payment_id}")
    builder.button(text="❌ Отклонить", callback_data=f"admin:payment_reject:{payment_id}")
    builder.adjust(1)
    return builder.as_markup()


def payment_confirm_final_keyboard(payment_id: int, amount: int) -> InlineKeyboardMarkup:
    """Клавиатура финального подтверждения платежа."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=f"admin:payment_confirm:{payment_id}:{amount}")
    builder.button(text="❌ Отмена", callback_data=f"admin:payment_cancel:{payment_id}")
    builder.adjust(2)
    return builder.as_markup()


def payment_reject_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения отклонения платежа."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, отклонить", callback_data=f"admin:payment_reject_confirm:{payment_id}")
    builder.button(text="❌ Нет, отмена", callback_data=f"admin:payment_cancel:{payment_id}")
    builder.adjust(2)
    return builder.as_markup()