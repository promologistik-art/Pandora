import logging
from datetime import date, timedelta

from aiogram import Router, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from sqlalchemy import select, func, text

from config import config
from database.engine import async_session
from database.models import Client, Subscription, Payment, EventLog
from services.client_service import (
    get_or_create_client, get_active_subscription,
    is_admin, add_referral_bonus, get_free_sub_link
)
from keyboards.admin_kb import admin_keyboard, admin_main_keyboard
from keyboards.client_kb import main_keyboard

logger = logging.getLogger(__name__)
router = Router()


# ========================
# FSM для рассылки
# ========================

class BroadcastState(StatesGroup):
    waiting_text = State()


# ========================
# Фильтр для админов
# ========================

class AdminFilter:
    async def __call__(self, message: types.Message):
        return is_admin(message.from_user.id)


# ========================
# Команда /admin — вход в админку
# ========================

@router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    await message.answer(
        "<b>⚙️ Админ-панель</b>\n\n"
        "Выберите действие:",
        reply_markup=admin_keyboard()
    )


# ========================
# Подтверждение платежа
# ========================

@router.message(Command("confirm"))
async def confirm_payment(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "📝 <b>Использование:</b>\n"
            "/confirm [payment_id] [сумма]\n\n"
            "Пример: /confirm 123 300"
        )
        return

    payment_id = int(args[1])
    amount = int(args[2]) if len(args) > 2 else 300

    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await message.answer("❌ Платёж не найден.")
            return

        payment.status = "confirmed"
        payment.amount = amount
        payment.confirmed_at = date.today()

        client = await session.get(Client, payment.client_id)
        if not client:
            await message.answer("❌ Клиент не найден.")
            return

        # Определяем тариф по сумме
        tariff_key = "1month"
        for key, t in config.TARIFFS.items():
            if t["price"] == amount:
                tariff_key = key
                break

        tariff = config.TARIFFS.get(tariff_key, config.TARIFFS["1month"])

        # Проверяем наличие активной подписки
        existing_sub = await get_active_subscription(client.id)
        if existing_sub:
            # Продлеваем существующую
            existing_sub.expires_at = existing_sub.expires_at + timedelta(days=tariff["days"])
            existing_sub.plan = tariff_key
            sub = existing_sub
        else:
            # Создаём новую
            sub_link = await get_free_sub_link(session)
            sub = Subscription(
                client_id=client.id,
                started_at=date.today(),
                expires_at=date.today() + timedelta(days=tariff["days"]),
                plan=tariff_key,
                sub_link=sub_link,
            )
            session.add(sub)

        event = EventLog(
            client_id=client.id,
            event_type="payment_confirmed",
            description=f"Платёж {amount} руб. подтверждён, подписка до {sub.expires_at}"
        )
        session.add(event)
        await session.commit()

    # Уведомляем клиента
    try:
        await message.bot.send_message(
            client.telegram_id,
            f"<b>✅ Оплата подтверждена!</b>\n\n"
            f"<b>Тариф:</b> {tariff['name']}\n"
            f"<b>Подписка до:</b> {sub.expires_at.strftime('%d.%m.%Y')}\n\n"
            f"<b>Ваша ссылка:</b>\n"
            f"<code>{sub.sub_link or 'не назначена'}</code>\n\n"
            f"<b>Поддержка:</b> @{config.SUPPORT_BOT_USERNAME}"
        )
        await message.answer(f"✅ Платёж #{payment_id} подтверждён. Клиент уведомлён.")
    except Exception as e:
        await message.answer(f"⚠️ Платёж подтверждён, но клиента уведомить не удалось: {e}")


# ========================
# Отклонение платежа
# ========================

@router.message(Command("reject"))
async def reject_payment(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("📝 Использование: /reject [payment_id]")
        return

    payment_id = int(args[1])

    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await message.answer("❌ Платёж не найден.")
            return

        payment.status = "rejected"
        await session.commit()

    await message.answer(f"❌ Платёж #{payment_id} отклонён.")


# ========================
# Управление пользователем
# ========================

@router.message(Command("user"))
async def manage_user(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("📝 Использование: /user [ID клиента]")
        return

    user_id = int(args[1])

    async with async_session() as session:
        client = await session.get(Client, user_id)
        if not client:
            await message.answer("❌ Клиент не найден.")
            return

        sub = await get_active_subscription(client.id)
        sub_text = f"до {sub.expires_at.strftime('%d.%m.%Y')}" if sub else "нет"

        text = (
            f"<b>👤 Клиент #{client.id}</b>\n"
            f"<b>Имя:</b> {client.first_name}\n"
            f"<b>Username:</b> @{client.username or 'нет'}\n"
            f"<b>Подписка:</b> {sub_text}\n\n"
            f"<b>Доступные команды:</b>\n"
            f"/extend {client.id} [дней] — продлить подписку\n"
            f"/delsub {client.id} — удалить подписку\n"
            f"/cleansub {client.id} — очистить истекшие подписки\n"
            f"/deluser {client.id} — удалить клиента"
        )
    await message.answer(text)


# ========================
# Продление подписки
# ========================

@router.message(Command("extend"))
async def extend_subscription(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("📝 Использование: /extend [ID клиента] [количество дней]")
        return

    user_id = int(args[1])
    days = int(args[2])

    async with async_session() as session:
        client = await session.get(Client, user_id)
        if not client:
            await message.answer("❌ Клиент не найден.")
            return

        sub = await get_active_subscription(client.id)
        if sub:
            sub.expires_at = sub.expires_at + timedelta(days=days)
        else:
            sub_link = await get_free_sub_link(session)
            sub = Subscription(
                client_id=client.id,
                started_at=date.today(),
                expires_at=date.today() + timedelta(days=days),
                plan="1month",
                sub_link=sub_link,
            )
            session.add(sub)

        event = EventLog(
            client_id=client.id,
            event_type="subscription_extended",
            description=f"Подписка продлена на {days} дн."
        )
        session.add(event)
        await session.commit()

    await message.answer(f"✅ Подписка клиента #{user_id} продлена до {sub.expires_at.strftime('%d.%m.%Y')}.")


# ========================
# Удаление пользователя
# ========================

@router.message(Command("deluser"))
async def delete_user(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("📝 Использование: /deluser [ID клиента]")
        return

    user_id = int(args[1])

    async with async_session() as session:
        client = await session.get(Client, user_id)
        if not client:
            await message.answer("❌ Клиент не найден.")
            return
        await session.delete(client)
        await session.commit()

    await message.answer(f"✅ Клиент #{user_id} удалён.")


# ========================
# Удаление подписки
# ========================

@router.message(Command("delsub"))
async def delete_subscription(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("📝 Использование: /delsub [ID клиента]")
        return

    user_id = int(args[1])

    async with async_session() as session:
        result = await session.execute(
            select(Subscription)
            .where(Subscription.client_id == user_id)
            .where(Subscription.status == "active")
        )
        subs = result.scalars().all()
        for s in subs:
            s.status = "cancelled"
        await session.commit()

    await message.answer(f"✅ Активные подписки клиента #{user_id} удалены.")


# ========================
# Очистка истекших подписок клиента
# ========================

@router.message(Command("cleansub"))
async def clean_user_subscriptions(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("📝 Использование: /cleansub [ID клиента]")
        return

    user_id = int(args[1])

    async with async_session() as session:
        await session.execute(
            text("UPDATE subscriptions SET status = 'expired' WHERE client_id = :uid AND status = 'active' AND expires_at < CURRENT_DATE"),
            {"uid": user_id}
        )
        await session.commit()

    await message.answer(f"✅ Истекшие подписки клиента #{user_id} очищены.")


# ========================
# Массовая очистка истекших подписок
# ========================

@router.message(Command("cleanup"))
async def cleanup_subscriptions(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    async with async_session() as session:
        await session.execute(
            text("UPDATE subscriptions SET status = 'expired' WHERE status = 'active' AND expires_at < CURRENT_DATE")
        )
        await session.commit()

    await message.answer("✅ Истекшие подписки очищены.")


# ========================
# ADMIN CALLBACK HANDLERS
# ========================

@router.callback_query(F.data == "admin:clients")
async def list_clients(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    async with async_session() as session:
        result = await session.execute(
            select(Client).order_by(Client.created_at.desc()).limit(20)
        )
        clients = result.scalars().all()

    if not clients:
        await callback.message.answer("📭 Клиентов пока нет.")
    else:
        text = "<b>👥 Последние 20 клиентов:</b>\n\n"
        for c in clients:
            sub = await get_active_subscription(c.id)
            sub_text = f"до {sub.expires_at.strftime('%d.%m')}" if sub else "нет подписки"
            text += (
                f"<b>ID:</b> <code>{c.id}</code> | @{c.username or 'нет'}\n"
                f"  {c.first_name} | {sub_text}\n\n"
            )
        text += "Для управления: /user [ID]"
        await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "admin:stats")
async def show_stats(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    async with async_session() as session:
        total_clients = await session.scalar(select(func.count(Client.id)))
        active_subs = await session.scalar(
            select(func.count(Subscription.id))
            .where(Subscription.status == "active")
            .where(Subscription.expires_at >= date.today())
        )
        total_payments = await session.scalar(
            select(func.sum(Payment.amount)).where(Payment.status == "confirmed")
        )
        month_payments = await session.scalar(
            select(func.sum(Payment.amount))
            .where(Payment.status == "confirmed")
            .where(func.date_trunc("month", Payment.confirmed_at) == func.date_trunc("month", func.now()))
        )

    await callback.message.answer(
        "<b>📊 Статистика</b>\n\n"
        f"<b>Всего клиентов:</b> {total_clients or 0}\n"
        f"<b>Активных подписок:</b> {active_subs or 0}\n"
        f"<b>Выручка за всё время:</b> {total_payments or 0} руб.\n"
        f"<b>Выручка за месяц:</b> {month_payments or 0} руб."
    )
    await callback.answer()


@router.callback_query(F.data == "admin:server")
async def server_status(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    from services.xray_api import xray

    try:
        if await xray.check_health():
            await callback.message.answer(
                "<b>🖥 Статус сервера</b>\n\n"
                "3x-ui: <b>✅ онлайн</b>\n"
                f"Адрес: {config.XUI_HOST}"
            )
        else:
            await callback.message.answer(
                "<b>🖥 Статус сервера</b>\n\n"
                "3x-ui: <b>❌ недоступен</b>\n"
                f"Адрес: {config.XUI_HOST}"
            )
    except Exception as e:
        await callback.message.answer(
            "<b>🖥 Статус сервера</b>\n\n"
            f"3x-ui: <b>❌ ошибка</b>\n"
            f"{e}"
        )
    await callback.answer()


@router.callback_query(F.data == "admin:broadcast")
async def broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    await callback.message.answer(
        "📢 <b>Рассылка</b>\n\n"
        "Введите сообщение для рассылки всем клиентам.\n"
        "Для отмены введите /cancel"
    )
    await state.set_state(BroadcastState.waiting_text)
    await callback.answer()


@router.message(BroadcastState.waiting_text, F.text)
async def broadcast_send(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    text = message.text

    async with async_session() as session:
        result = await session.execute(select(Client.telegram_id))
        clients = result.scalars().all()

    success = 0
    for tid in clients:
        try:
            await message.bot.send_message(
                tid,
                f"📢 <b>Рассылка</b>\n\n{text}"
            )
            success += 1
        except Exception:
            pass

    await message.answer(
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"Доставлено: {success}/{len(clients)} клиентов."
    )
    await state.clear()


@router.callback_query(F.data == "admin:cleanup")
async def cleanup_subscriptions_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    async with async_session() as session:
        await session.execute(
            text("UPDATE subscriptions SET status = 'expired' WHERE status = 'active' AND expires_at < CURRENT_DATE")
        )
        await session.commit()

    await callback.message.answer("✅ Истекшие подписки очищены.")
    await callback.answer()


@router.callback_query(F.data == "admin:exit")
async def exit_admin(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    await callback.message.answer(
        "👋 Выход из админки.",
        reply_markup=admin_main_keyboard()
    )
    await callback.answer()


@router.message(Command("cancel"))
async def cancel_action(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("❌ Действие отменено.")
    else:
        await message.answer("Нет активных действий для отмены.")