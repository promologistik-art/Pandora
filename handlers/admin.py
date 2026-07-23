import logging
from datetime import date, timedelta

from aiogram import Router, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardRemove

from sqlalchemy import select, func, text

from config import config
from database.engine import async_session
from database.models import Client, Subscription, Payment, EventLog
from services.client_service import (
    get_or_create_client, get_active_subscription,
    is_admin, get_free_sub_link
)
from keyboards.admin_kb import (
    admin_keyboard, user_profile_keyboard, confirm_keyboard,
    confirm_extend_keyboard, payment_confirm_keyboard,
    payment_confirm_final_keyboard, payment_reject_keyboard
)
from keyboards.client_kb import main_keyboard, admin_main_keyboard

logger = logging.getLogger(__name__)
router = Router()


# ========================
# FSM СОСТОЯНИЯ
# ========================

class AdminStates(StatesGroup):
    waiting_extend_days = State()
    waiting_payment_amount = State()
    waiting_broadcast_text = State()


# ========================
# КОМАНДА /admin
# ========================

@router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Недостаточно прав.")
        return
    await message.answer(
        "<b>⚙️ Админ-панель</b>\n\n"
        "Выберите действие:",
        reply_markup=admin_keyboard()
    )


# ========================
# СПИСОК КЛИЕНТОВ
# ========================

@router.callback_query(F.data == "admin:clients")
async def list_clients(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    async with async_session() as session:
        result = await session.execute(
            select(Client)
            .where(Client.status == "active")
            .order_by(Client.created_at.desc())
            .limit(20)
        )
        clients = result.scalars().all()

    if not clients:
        await callback.message.answer("📭 Активных клиентов пока нет.")
    else:
        text = "<b>👥 Последние 20 клиентов:</b>\n\n"
        for c in clients:
            sub = await get_active_subscription(c.id)
            sub_text = f"до {sub.expires_at.strftime('%d.%m')}" if sub else "нет подписки"
            text += (
                f"<b>ID:</b> <code>{c.id}</code> | @{c.username or 'нет'}\n"
                f"  {c.first_name} | {sub_text}\n\n"
            )
        text += "Нажмите на кнопку ниже для управления:"
        await callback.message.answer(text)

        # Показываем кнопки для каждого клиента
        for c in clients[:5]:  # Ограничим 5 кнопками, чтобы не заспамить
            sub = await get_active_subscription(c.id)
            status_text = "✅" if sub else "❌"
            await callback.message.answer(
                f"{status_text} <b>ID {c.id}</b> — @{c.username or c.first_name}",
                reply_markup=user_profile_keyboard(c.id, bool(sub))
            )

    await callback.answer()


# ========================
# ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ (через /user)
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

        if client.status == "banned":
            await message.answer(f"🚫 Клиент #{user_id} заблокирован.")
            return

        sub = await get_active_subscription(client.id)
        sub_text = f"до {sub.expires_at.strftime('%d.%m.%Y')}" if sub else "нет"

        text = (
            f"<b>👤 Клиент #{client.id}</b>\n"
            f"<b>Имя:</b> {client.first_name}\n"
            f"<b>Username:</b> @{client.username or 'нет'}\n"
            f"<b>Подписка:</b> {sub_text}\n\n"
            f"<b>Выберите действие:</b>"
        )
    await message.answer(text, reply_markup=user_profile_keyboard(user_id, bool(sub)))


# ========================
# ПРОДЛИТЬ ПОДПИСКУ
# ========================

@router.callback_query(F.data.startswith("admin:extend:"))
async def extend_subscription_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    client_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        client = await session.get(Client, client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
            return

        await state.update_data(client_id=client_id, username=client.username or client.first_name)
        await callback.message.answer(
            f"📅 Введите количество дней для продления @{client.username or client.first_name}:"
        )
        await state.set_state(AdminStates.waiting_extend_days)
        await callback.answer()


@router.message(AdminStates.waiting_extend_days, F.text)
async def extend_subscription_days(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    try:
        days = int(message.text.strip())
        if days <= 0:
            await message.answer("❌ Количество дней должно быть положительным числом. Попробуйте снова:")
            return
    except ValueError:
        await message.answer("❌ Введите число. Попробуйте снова:")
        return

    data = await state.get_data()
    client_id = data.get("client_id")
    username = data.get("username")

    await state.update_data(days=days)

    await message.answer(
        f"📅 Продлеваем @{username} на <b>{days} дней</b>.\n\n"
        f"Подтвердите действие:",
        reply_markup=confirm_extend_keyboard(client_id, days)
    )
    await state.clear()


@router.callback_query(F.data.startswith("admin:confirm_extend:"))
async def extend_subscription_confirm(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    _, _, client_id, days = callback.data.split(":")
    client_id = int(client_id)
    days = int(days)

    async with async_session() as session:
        client = await session.get(Client, client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
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
            description=f"Подписка продлена на {days} дн. админом"
        )
        session.add(event)
        await session.commit()

    await callback.message.edit_text(
        f"✅ Подписка @{client.username or client.first_name} продлена до {sub.expires_at.strftime('%d.%m.%Y')}."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:cancel_extend:"))
async def extend_subscription_cancel(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    await callback.message.edit_text("❌ Продление отменено.")
    await callback.answer()


# ========================
# УДАЛИТЬ ПОДПИСКУ
# ========================

@router.callback_query(F.data.startswith("admin:delsub:"))
async def delete_subscription(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    client_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        client = await session.get(Client, client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
            return

        sub = await get_active_subscription(client.id)
        if not sub:
            await callback.message.answer(f"❌ У @{client.username or client.first_name} нет активной подписки.")
            await callback.answer()
            return

        await callback.message.answer(
            f"❌ Удаляем подписку у @{client.username or client.first_name}?\n\n"
            f"Подписка действует до: {sub.expires_at.strftime('%d.%m.%Y')}",
            reply_markup=confirm_keyboard("delsub", client_id)
        )
        await callback.answer()


@router.callback_query(F.data.startswith("admin:confirm:delsub:"))
async def delete_subscription_confirm(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    _, _, _, client_id = callback.data.split(":")
    client_id = int(client_id)

    async with async_session() as session:
        client = await session.get(Client, client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
            return

        sub = await get_active_subscription(client.id)
        if sub:
            sub.status = "cancelled"
            await session.commit()

            event = EventLog(
                client_id=client.id,
                event_type="subscription_deleted",
                description=f"Подписка удалена админом"
            )
            session.add(event)
            await session.commit()

            # Уведомляем клиента
            try:
                await callback.bot.send_message(
                    client.telegram_id,
                    f"❌ <b>Ваша подписка удалена администратором.</b>\n\n"
                    f"Свяжитесь с поддержкой: @{config.SUPPORT_BOT_USERNAME}"
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить клиента {client.id}: {e}")

    await callback.message.edit_text(f"✅ Подписка @{client.username or client.first_name} удалена.")
    await callback.answer()


# ========================
# ОЧИСТИТЬ ИСТЕКШИЕ
# ========================

@router.callback_query(F.data.startswith("admin:cleansub:"))
async def clean_subscriptions(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    client_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        client = await session.get(Client, client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
            return

        await callback.message.answer(
            f"🧹 Очищаем истекшие подписки у @{client.username or client.first_name}?",
            reply_markup=confirm_keyboard("cleansub", client_id)
        )
        await callback.answer()


@router.callback_query(F.data.startswith("admin:confirm:cleansub:"))
async def clean_subscriptions_confirm(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    _, _, _, client_id = callback.data.split(":")
    client_id = int(client_id)

    async with async_session() as session:
        client = await session.get(Client, client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
            return

        await session.execute(
            text("UPDATE subscriptions SET status = 'cleaned' WHERE client_id = :uid AND status = 'expired'"),
            {"uid": client_id}
        )
        await session.commit()

        event = EventLog(
            client_id=client.id,
            event_type="subscriptions_cleaned",
            description=f"Истекшие подписки очищены админом"
        )
        session.add(event)
        await session.commit()

    await callback.message.edit_text(f"✅ Истекшие подписки @{client.username or client.first_name} очищены.")
    await callback.answer()


# ========================
# ЗАБЛОКИРОВАТЬ КЛИЕНТА
# ========================

@router.callback_query(F.data.startswith("admin:ban:"))
async def ban_user(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    client_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        client = await session.get(Client, client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
            return

        if client.status == "banned":
            await callback.message.answer(f"🚫 @{client.username or client.first_name} уже заблокирован.")
            await callback.answer()
            return

        await callback.message.answer(
            f"⚠️ <b>БЛОКИРУЕМ клиента @{client.username or client.first_name}?</b>\n\n"
            f"Все подписки будут отключены.\n"
            f"Клиент получит уведомление.",
            reply_markup=confirm_keyboard("ban", client_id)
        )
        await callback.answer()


@router.callback_query(F.data.startswith("admin:confirm:ban:"))
async def ban_user_confirm(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    _, _, _, client_id = callback.data.split(":")
    client_id = int(client_id)

    async with async_session() as session:
        client = await session.get(Client, client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
            return

        client.status = "banned"

        # Блокируем все подписки
        await session.execute(
            text("UPDATE subscriptions SET status = 'banned' WHERE client_id = :uid AND status = 'active'"),
            {"uid": client_id}
        )
        await session.commit()

        event = EventLog(
            client_id=client.id,
            event_type="user_banned",
            description=f"Клиент заблокирован админом"
        )
        session.add(event)
        await session.commit()

        # Уведомляем клиента
        try:
            await callback.bot.send_message(
                client.telegram_id,
                f"🚫 <b>Ваш доступ заблокирован.</b>\n\n"
                f"Свяжитесь с поддержкой: @{config.SUPPORT_BOT_USERNAME}"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить клиента {client.id}: {e}")

    await callback.message.edit_text(f"✅ Клиент @{client.username or client.first_name} заблокирован.")
    await callback.answer()


# ========================
# РАЗБЛОКИРОВАТЬ КЛИЕНТА
# ========================

@router.callback_query(F.data.startswith("admin:unban:"))
async def unban_user(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    client_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        client = await session.get(Client, client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
            return

        if client.status != "banned":
            await callback.message.answer(f"✅ @{client.username or client.first_name} не заблокирован.")
            await callback.answer()
            return

        await callback.message.answer(
            f"✅ Разблокируем клиента @{client.username or client.first_name}?",
            reply_markup=confirm_keyboard("unban", client_id)
        )
        await callback.answer()


@router.callback_query(F.data.startswith("admin:confirm:unban:"))
async def unban_user_confirm(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    _, _, _, client_id = callback.data.split(":")
    client_id = int(client_id)

    async with async_session() as session:
        client = await session.get(Client, client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
            return

        client.status = "active"
        await session.commit()

        event = EventLog(
            client_id=client.id,
            event_type="user_unbanned",
            description=f"Клиент разблокирован админом"
        )
        session.add(event)
        await session.commit()

        # Уведомляем клиента
        try:
            await callback.bot.send_message(
                client.telegram_id,
                f"✅ <b>Ваш доступ восстановлен.</b>\n\n"
                f"Если у вас есть вопросы: @{config.SUPPORT_BOT_USERNAME}"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить клиента {client.id}: {e}")

    await callback.message.edit_text(f"✅ Клиент @{client.username or client.first_name} разблокирован.")
    await callback.answer()


# ========================
# ОТМЕНА ДЕЙСТВИЙ
# ========================

@router.callback_query(F.data.startswith("admin:cancel:"))
async def cancel_action(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    await callback.message.edit_text("❌ Действие отменено.")
    await callback.answer()


# ========================
# СТАТИСТИКА
# ========================

@router.callback_query(F.data == "admin:stats")
async def show_stats(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    async with async_session() as session:
        total_clients = await session.scalar(
            select(func.count(Client.id)).where(Client.status == "active")
        )
        banned_clients = await session.scalar(
            select(func.count(Client.id)).where(Client.status == "banned")
        )
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
        f"<b>Заблокировано:</b> {banned_clients or 0}\n"
        f"<b>Активных подписок:</b> {active_subs or 0}\n"
        f"<b>Выручка за всё время:</b> {total_payments or 0} руб.\n"
        f"<b>Выручка за месяц:</b> {month_payments or 0} руб."
    )
    await callback.answer()


# ========================
# СТАТУС СЕРВЕРА
# ========================

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


# ========================
# РАССЫЛКА
# ========================

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
    await state.set_state(AdminStates.waiting_broadcast_text)
    await callback.answer()


@router.message(AdminStates.waiting_broadcast_text, F.text)
async def broadcast_send(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    text = message.text

    async with async_session() as session:
        result = await session.execute(
            select(Client.telegram_id).where(Client.status == "active")
        )
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


# ========================
# ОЧИСТКА ИСТЕКШИХ (МАССОВАЯ)
# ========================

@router.callback_query(F.data == "admin:cleanup")
async def cleanup_subscriptions(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    async with async_session() as session:
        await session.execute(
            text("UPDATE subscriptions SET status = 'cleaned' WHERE status = 'expired'")
        )
        await session.commit()

    await callback.message.answer("✅ Истекшие подписки очищены.")
    await callback.answer()


# ========================
# ПОДТВЕРЖДЕНИЕ ПЛАТЕЖА
# ========================

@router.callback_query(F.data.startswith("admin:payment_amount:"))
async def payment_amount_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    payment_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await callback.message.answer("❌ Платёж не найден.")
            await callback.answer()
            return

        client = await session.get(Client, payment.client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
            return

        await state.update_data(payment_id=payment_id, client_id=client.id, username=client.username or client.first_name)
        await callback.message.answer(
            f"💰 Введите сумму платежа для @{client.username or client.first_name} (только цифры):"
        )
        await state.set_state(AdminStates.waiting_payment_amount)
        await callback.answer()


@router.message(AdminStates.waiting_payment_amount, F.text)
async def payment_amount_enter(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    try:
        amount = int(message.text.strip())
        if amount <= 0:
            await message.answer("❌ Сумма должна быть положительным числом. Попробуйте снова:")
            return
    except ValueError:
        await message.answer("❌ Введите число. Попробуйте снова:")
        return

    data = await state.get_data()
    payment_id = data.get("payment_id")
    username = data.get("username")

    await message.answer(
        f"💰 Подтверждаем платёж @{username} на <b>{amount} руб.</b>\n\n"
        f"Подтвердите действие:",
        reply_markup=payment_confirm_final_keyboard(payment_id, amount)
    )
    await state.clear()


@router.callback_query(F.data.startswith("admin:payment_confirm:"))
async def payment_confirm(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    _, _, payment_id, amount = callback.data.split(":")
    payment_id = int(payment_id)
    amount = int(amount)

    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await callback.message.answer("❌ Платёж не найден.")
            await callback.answer()
            return

        payment.status = "confirmed"
        payment.amount = amount
        payment.confirmed_at = date.today()

        client = await session.get(Client, payment.client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
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
            existing_sub.expires_at = existing_sub.expires_at + timedelta(days=tariff["days"])
            existing_sub.plan = tariff_key
            sub = existing_sub
        else:
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
        await callback.bot.send_message(
            client.telegram_id,
            f"<b>✅ Оплата подтверждена!</b>\n\n"
            f"<b>Тариф:</b> {tariff['name']}\n"
            f"<b>Подписка до:</b> {sub.expires_at.strftime('%d.%m.%Y')}\n\n"
            f"<b>Ваша ссылка:</b>\n"
            f"<code>{sub.sub_link or 'не назначена'}</code>\n\n"
            f"<b>Поддержка:</b> @{config.SUPPORT_BOT_USERNAME}"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить клиента {client.id}: {e}")

    await callback.message.edit_text(f"✅ Платёж #{payment_id} подтверждён на {amount} руб. Клиент уведомлён.")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:payment_reject:"))
async def payment_reject_start(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    payment_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await callback.message.answer("❌ Платёж не найден.")
            await callback.answer()
            return

        client = await session.get(Client, payment.client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
            return

        await callback.message.answer(
            f"❌ Отклоняем платёж @{client.username or client.first_name}?",
            reply_markup=payment_reject_keyboard(payment_id)
        )
        await callback.answer()


@router.callback_query(F.data.startswith("admin:payment_reject_confirm:"))
async def payment_reject_confirm(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    payment_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await callback.message.answer("❌ Платёж не найден.")
            await callback.answer()
            return

        payment.status = "rejected"

        client = await session.get(Client, payment.client_id)
        if not client:
            await callback.message.answer("❌ Клиент не найден.")
            await callback.answer()
            return

        await session.commit()

        # Уведомляем клиента
        try:
            await callback.bot.send_message(
                client.telegram_id,
                f"❌ <b>Ваш платёж отклонён.</b>\n\n"
                f"Свяжитесь с поддержкой: @{config.SUPPORT_BOT_USERNAME}"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить клиента {client.id}: {e}")

    await callback.message.edit_text(f"❌ Платёж #{payment_id} отклонён. Клиент уведомлён.")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:payment_cancel:"))
async def payment_cancel(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    await callback.message.edit_text("❌ Действие отменено.")
    await callback.answer()


# ========================
# ОТМЕНА (COMMAND)
# ========================

@router.message(Command("cancel"))
async def cancel_command(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("❌ Действие отменено.")
    else:
        await message.answer("Нет активных действий для отмены.")