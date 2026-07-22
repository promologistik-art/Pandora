import logging
from datetime import date, timedelta

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardRemove

from sqlalchemy import select, func

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


# === Вход в админку ===

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


# === Подтверждение платежа ===

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

        # Ищем клиента
        client = await session.get(Client, payment.client_id)
        if not client:
            await message.answer("Клиент не найден.")
            return

        # Определяем тариф по сумме
        tariff_key = "1month"
        for key, t in config.TARIFFS.items():
            if t["price"] == amount:
                tariff_key = key
                break

        tariff = config.TARIFFS.get(tariff_key, config.TARIFFS["1month"])

        # Создаём или продлеваем подписку
        active_sub = await session.execute(
            select(Subscription)
            .where(Subscription.client_id == client.id)
            .where(Subscription.status == "active")
            .order_by(Subscription.expires_at.desc())
            .limit(1)
        )
        sub = active_sub.scalar_one_or_none()

        if sub and sub.expires_at >= date.today():
            # Продлеваем
            sub.expires_at = sub.expires_at + timedelta(days=tariff["days"])
            sub.plan = tariff_key
        else:
            # Новая подписка
            if sub:
                sub.status = "cancelled"

            new_uuid = Subscription.generate_uuid()
            xray_result = await xray.add_client(
                email=f"client_{client.id}",
                uuid=new_uuid
            )

            sub = Subscription(
                client_id=client.id,
                started_at=date.today(),
                expires_at=date.today() + timedelta(days=tariff["days"]),
                plan=tariff_key,
                xray_uuid=new_uuid,
            )
            session.add(sub)

        # Логируем
        event = EventLog(
            client_id=client.id,
            event_type="payment_confirmed",
            description=f"Платёж {amount}₽ подтверждён, подписка до {sub.expires_at}"
        )
        session.add(event)
        await session.commit()

    # Уведомляем клиента
    host = config.XUI_HOST.split("://")[1].split(":")[0] if "://" in config.XUI_HOST else config.XUI_HOST
    vless_link = f"vless://{sub.xray_uuid}@{host}:443?encryption=none&security=reality&type=tcp&flow=xtls-rprx-vision#Pandora"

    try:
        await message.bot.send_message(
            client.telegram_id,
            f"<b>Оплата подтверждена!</b>\n\n"
            f"Тариф: {tariff['name']}\n"
            f"Подписка до: {sub.expires_at.strftime('%d.%m.%Y')}\n\n"
            "<b>Ваш ключ:</b>\n"
            f"<code>{vless_link}</code>\n\n"
            f"Поддержка: @{config.SUPPORT_BOT_USERNAME}"
        )
        await message.answer(f"Платёж #{payment_id} подтверждён. Клиент уведомлён.")
    except Exception as e:
        await message.answer(f"Платёж подтверждён, но клиента уведомить не удалось: {e}")


# === Отклонение платежа ===

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


# === Список клиентов ===

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


# === Статистика ===

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
        f"<b>Выручка за всё время:</b> {total_payments or 0}₽\n"
        f"<b>Выручка за месяц:</b> {month_payments or 0}₽"
    )


# === Статус сервера ===

@router.message(F.text == "🖥 Сервер")
async def server_status(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    # Проверяем доступность 3x-ui
    try:
        logged_in = await xray.login()
        if logged_in:
            await message.answer(
                "<b>🖥 Статус сервера</b>\n\n"
                "3x-ui: <b>онлайн</b> ✅\n"
                f"Адрес: {config.XUI_HOST}"
            )
        else:
            await message.answer(
                "<b>🖥 Статус сервера</b>\n\n"
                "3x-ui: <b>ошибка подключения</b> ❌\n"
                f"Адрес: {config.XUI_HOST}"
            )
    except Exception as e:
        await message.answer(
            "<b>🖥 Статус сервера</b>\n\n"
            f"3x-ui: <b>ошибка</b> ❌\n"
            f"{e}"
        )


# === Рассылка ===

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