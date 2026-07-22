import logging
from datetime import date, timedelta

from aiogram import Router, types, F
from aiogram.filters import Command

from sqlalchemy import select, func, text

from config import config
from database.engine import async_session
from database.models import Client, Subscription, Payment, EventLog
from services.xray_api import xray
from keyboards.admin_kb import admin_keyboard
from keyboards.client_kb import main_keyboard

logger = logging.getLogger(__name__)
router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


@router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return

    await message.answer(
        "<b>Админ-панель</b>",
        reply_markup=admin_keyboard()
    )


@router.message(F.text == "🔙 Выйти из админки")
async def exit_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Выход из админки.", reply_markup=main_keyboard())


@router.message(Command("cleanup"))
async def cleanup_subscriptions(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    async with async_session() as session:
        await session.execute(
            text("UPDATE subscriptions SET status = 'expired' WHERE status = 'active' AND expires_at < CURRENT_DATE")
        )
        await session.commit()
    await message.answer("Истекшие подписки очищены.")


@router.message(Command("confirm"))
async def confirm_payment(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /confirm [payment_id] [сумма]")
        return

    payment_id = int(args[1])
    amount = int(args[2]) if len(args) > 2 else 300

    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await message.answer("Платёж не найден.")
            return

        payment.status = "confirmed"
        payment.amount = amount
        payment.confirmed_at = date.today()

        client = await session.get(Client, payment.client_id)
        if not client:
            await message.answer("Клиент не найден.")
            return

        tariff_key = "1month"
        for key, t in config.TARIFFS.items():
            if t["price"] == amount:
                tariff_key = key
                break

        tariff = config.TARIFFS.get(tariff_key, config.TARIFFS["1month"])

        # Ищем свободную ссылку
        used_links = await session.execute(
            select(Subscription.sub_link)
            .where(Subscription.status == "active")
            .where(Subscription.expires_at >= date.today())
        )
        used = set(row[0] for row in used_links if row[0])
        free_links = [link for link in config.SUB_LINKS if link not in used]
        sub_link = free_links[0] if free_links else None

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

    try:
        await message.bot.send_message(
            client.telegram_id,
            f"<b>Оплата подтверждена!</b>\n\n"
            f"Тариф: {tariff['name']}\n"
            f"Подписка до: {sub.expires_at.strftime('%d.%m.%Y')}\n\n"
            f"<b>Ваша ссылка:</b>\n"
            f"<code>{sub_link or 'не назначена'}</code>\n\n"
            f"Поддержка: @{config.SUPPORT_BOT_USERNAME}"
        )
        await message.answer(f"Платёж #{payment_id} подтверждён. Клиент уведомлён.")
    except Exception as e:
        await message.answer(f"Платёж подтверждён, но клиента уведомить не удалось: {e}")


@router.message(Command("reject"))
async def reject_payment(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /reject [payment_id]")
        return

    payment_id = int(args[1])

    async with async_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await message.answer("Платёж не найден.")
            return

        payment.status = "rejected"
        await session.commit()

    await message.answer(f"Платёж #{payment_id} отклонён.")


@router.message(F.text == "👥 Клиенты")
async def list_clients(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    async with async_session() as session:
        result = await session.execute(
            select(Client).order_by(Client.created_at.desc()).limit(20)
        )
        clients = result.scalars().all()

    if not clients:
        await message.answer("Клиентов пока нет.")
        return

    text = "<b>Последние 20 клиентов:</b>\n\n"
    for c in clients:
        sub = await get_active_sub(c.id)
        sub_text = f"до {sub.expires_at.strftime('%d.%m')}" if sub else "нет подписки"
        text += (
            f"ID: <code>{c.id}</code> | @{c.username or 'нет'}\n"
            f"  {c.first_name} | {sub_text}\n"
        )

    await message.answer(text)


async def get_active_sub(client_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(Subscription)
            .where(Subscription.client_id == client_id)
            .where(Subscription.status == "active")
            .where(Subscription.expires_at >= date.today())
            .order_by(Subscription.expires_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


@router.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    async with async_session() as session:
        total_clients = await session.scalar(select(func.count(Client.id)))
        active_subs = await session.scalar(
            select(func.count(Subscription.id))
            .where(Subscription.status == "active")
            .where(Subscription.expires_at >= date.today())
        )
        total_payments = await session.scalar(
            select(func.sum(Payment.amount))
            .where(Payment.status == "confirmed")
        )
        month_payments = await session.scalar(
            select(func.sum(Payment.amount))
            .where(Payment.status == "confirmed")
            .where(func.date_trunc("month", Payment.confirmed_at) == func.date_trunc("month", func.now()))
        )

    await message.answer(
        "<b>📊 Статистика</b>\n\n"
        f"<b>Всего клиентов:</b> {total_clients or 0}\n"
        f"<b>Активных подписок:</b> {active_subs or 0}\n"
        f"<b>Выручка за всё время:</b> {total_payments or 0} руб.\n"
        f"<b>Выручка за месяц:</b> {month_payments or 0} руб."
    )


@router.message(F.text == "🖥 Сервер")
async def server_status(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    try:
        data = await xray._api_get("/panel/api/inbounds/list")
        if data and data.get("success"):
            await message.answer(
                "<b>🖥 Статус сервера</b>\n\n"
                "3x-ui: <b>онлайн</b>\n"
                f"Адрес: {config.XUI_HOST}"
            )
        else:
            await message.answer(
                "<b>🖥 Статус сервера</b>\n\n"
                "3x-ui: <b>ошибка подключения</b>\n"
                f"Адрес: {config.XUI_HOST}"
            )
    except Exception as e:
        await message.answer(
            "<b>🖥 Статус сервера</b>\n\n"
            f"3x-ui: <b>ошибка</b>\n"
            f"{e}"
        )


@router.message(F.text == "📢 Рассылка")
async def broadcast_start(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "Введите сообщение для рассылки всем клиентам.\n"
        "Для отмены: /cancel"
    )


@router.message(Command("cancel"))
async def cancel_action(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Действие отменено.", reply_markup=admin_keyboard())
