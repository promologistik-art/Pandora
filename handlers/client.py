import logging
from datetime import date, timedelta, datetime

from aiogram import Router, types, F
from aiogram.filters import Command, CommandStart

from sqlalchemy import select

from config import config
from database.engine import async_session
from database.models import Client, Subscription, Payment, EventLog
from keyboards.client_kb import (
    main_keyboard, admin_main_keyboard, admin_keyboard,
    tariff_keyboard, payment_keyboard, status_keyboard,
    help_keyboard, downloads_keyboard, referral_keyboard
)

logger = logging.getLogger(__name__)
router = Router()


async def get_or_create_client(telegram_id: int, username: str, first_name: str) -> Client:
    async with async_session() as session:
        result = await session.execute(
            select(Client).where(Client.telegram_id == telegram_id)
        )
        client = result.scalar_one_or_none()

        if client is None:
            client = Client(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
            )
            session.add(client)
            await session.commit()
            await session.refresh(client)

            event = EventLog(
                client_id=client.id,
                event_type="client_created",
                description=f"Новый клиент: @{username} ({first_name})"
            )
            session.add(event)
            await session.commit()

            logger.info(f"Новый клиент: {client.id} (@{username})")

        return client


async def get_active_subscription(client_id: int) -> Subscription | None:
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


async def add_referral_bonus(referrer: Client, session):
    active_sub = await session.execute(
        select(Subscription)
        .where(Subscription.client_id == referrer.id)
        .where(Subscription.status == "active")
        .order_by(Subscription.expires_at.desc())
        .limit(1)
    )
    sub = active_sub.scalar_one_or_none()
    if sub:
        sub.expires_at = sub.expires_at + timedelta(days=config.REFERRAL_BONUS_DAYS)
        await session.commit()

    event = EventLog(
        client_id=referrer.id,
        event_type="referral_bonus",
        description=f"Начислено {config.REFERRAL_BONUS_DAYS} бонусных дней за реферала"
    )
    session.add(event)
    await session.commit()


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    client = await get_or_create_client(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    if client.created_at and (datetime.utcnow() - client.created_at).seconds < 10:
        for admin_id in config.ADMIN_IDS:
            try:
                await message.bot.send_message(
                    admin_id,
                    f"🆕 <b>Новый пользователь!</b>\n"
                    f"ID: {client.id}\n"
                    f"Имя: {client.first_name}\n"
                    f"Username: @{client.username or 'нет'}"
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить админа {admin_id}: {e}")

    ref_arg = None
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref"):
        try:
            ref_id = int(args[1][3:])
            if ref_id != client.id:
                ref_arg = ref_id
        except ValueError:
            pass

    if ref_arg and client.referrer_id is None:
        async with async_session() as session:
            referrer = await session.get(Client, ref_arg)
            if referrer:
                client.referrer_id = ref_arg
                client.source = "referral"
                await session.commit()
                await add_referral_bonus(referrer, session)

    welcome = (
        "<b>Ящик Пандоры</b> - стабильный VPN с умной маршрутизацией.\n"
        "Заблокированные сайты работают, белые списки не тормозят.\n\n"
        "<i>Нет Telegram?</i> Инструкции и поддержка ВКонтакте:\n"
        f"{config.VK_PAGE}"
    )

    if message.from_user.id in config.ADMIN_IDS:
        await message.answer(welcome, reply_markup=admin_main_keyboard())
    else:
        await message.answer(welcome, reply_markup=main_keyboard())


@router.message(F.text == "💳 Попробовать 3 дня бесплатно")
async def trial_start(message: types.Message):
    client = await get_or_create_client(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    async with async_session() as session:
        result = await session.execute(
            select(Subscription)
            .where(Subscription.client_id == client.id)
            .where(Subscription.status == "active")
            .where(Subscription.expires_at >= date.today())
        )
        if result.scalar_one_or_none():
            await message.answer(
                "У вас уже есть активная подписка.\n"
                "Проверьте статус: кнопка «📊 Статус»",
                reply_markup=main_keyboard()
            )
            return

        used_links = await session.execute(
            select(Subscription.sub_link)
            .where(Subscription.status == "active")
            .where(Subscription.expires_at >= date.today())
        )
        used = set(row[0] for row in used_links if row[0])

    free_links = [link for link in config.SUB_LINKS if link not in used]

    if not free_links:
        await message.answer(
            "К сожалению, все пробные места сейчас заняты.\n"
            "Попробуйте позже или свяжитесь с поддержкой.",
            reply_markup=main_keyboard()
        )
        return

    sub_link = free_links[0]

    async with async_session() as session:
        sub = Subscription(
            client_id=client.id,
            started_at=date.today(),
            expires_at=date.today() + timedelta(days=config.TRIAL_DAYS),
            plan="trial",
            is_trial=True,
            sub_link=sub_link,
        )
        session.add(sub)

        event = EventLog(
            client_id=client.id,
            event_type="trial_activated",
            description=f"Триал на {config.TRIAL_DAYS} дн., ссылка {sub_link}"
        )
        session.add(event)
        await session.commit()

    await message.answer(
        f"<b>Триал-доступ активирован на {config.TRIAL_DAYS} дня!</b>\n\n"
        f"<b>Ваша ссылка:</b>\n"
        f"<code>{sub_link}</code>\n\n"
        "<b>Как подключиться:</b>\n"
        "1. Скачайте приложение Happ (кнопка «🆘 Помощь / FAQ»)\n"
        "2. В приложении добавьте подписку:\n"
        "   Тип: Подписка\n"
        "   Имя: любое (например, Ящик Пандоры)\n"
        "   URL: скопируйте ссылку выше и вставьте\n"
        "3. Готово!\n\n"
        f"<b>Поддержка:</b> @{config.SUPPORT_BOT_USERNAME}",
        reply_markup=status_keyboard()
    )


@router.message(F.text == "📊 Статус")
async def cmd_status(message: types.Message):
    client = await get_or_create_client(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    sub = await get_active_subscription(client.id)

    if sub is None:
        await message.answer(
            "У вас нет активной подписки.\n"
            "Выберите действие:",
            reply_markup=main_keyboard()
        )
        return

    days_left = (sub.expires_at - date.today()).days
    trial_text = " (триал)" if sub.is_trial else ""
    link = sub.sub_link or "не указана"

    await message.answer(
        "<b>📊 Статус подписки</b>\n\n"
        f"<b>Статус:</b> активна{trial_text}\n"
        f"<b>Тариф:</b> {config.TARIFFS.get(sub.plan, {}).get('name', sub.plan)}\n"
        f"<b>Действует до:</b> {sub.expires_at.strftime('%d.%m.%Y')}\n"
        f"<b>Осталось дней:</b> {days_left}\n\n"
        "<b>Ваша ссылка:</b>\n"
        f"<code>{link}</code>\n\n"
        "<b>Как подключиться:</b>\n"
        "1. Скачайте приложение Happ (кнопка «🆘 Помощь / FAQ»)\n"
        "2. В приложении добавьте подписку:\n"
        "   Тип: Подписка\n"
        "   Имя: любое (например, Ящик Пандоры)\n"
        "   URL: скопируйте ссылку выше и вставьте\n"
        "3. Готово!\n\n"
        f"<b>Поддержка:</b> @{config.SUPPORT_BOT_USERNAME}",
        reply_markup=status_keyboard()
    )


@router.message(F.text == "🆘 Помощь / FAQ")
async def cmd_help(message: types.Message):
    await message.answer(
        "<b>🆘 Помощь и FAQ</b>\n\n"
        "<b>Частые вопросы:</b>\n"
        "- Не работает YouTube - попробуйте переподключиться\n"
        "- Медленная скорость - проверьте сервер в статусе\n"
        "- Как установить на устройство - см. инструкции ниже\n\n"
        f"<b>Поддержка:</b> @{config.SUPPORT_BOT_USERNAME}\n"
        f"<b>ВКонтакте:</b> {config.VK_PAGE}",
        reply_markup=help_keyboard()
    )


@router.callback_query(F.data == "help:downloads")
async def show_downloads(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "<b>📥 Скачать приложения:</b>\n\n"
        "Выберите платформу:",
        reply_markup=downloads_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("download:"))
async def send_download_link(callback: types.CallbackQuery):
    platform = callback.data.split(":")[1]

    links = {
        "windows": "https://www.happ.su/main/ru",
        "macos": "https://www.happ.su/main/ru",
        "android": "https://play.google.com/store/apps/details?id=com.happproxy",
        "ios": "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
        "androidtv": "https://play.google.com/store/apps/details?id=com.happproxy",
    }

    platform_names = {
        "windows": "Windows",
        "macos": "macOS",
        "android": "Android",
        "ios": "iOS",
        "androidtv": "Android TV",
    }

    name = platform_names.get(platform, platform)
    text = links.get(platform, "https://www.happ.su/main/ru")

    await callback.message.answer(
        f"<b>Скачать для {name}:</b>\n"
        f"{text}\n\n"
        f"Все версии: https://www.happ.su/main/ru"
    )
    await callback.answer()


@router.callback_query(F.data == "help:instructions")
async def send_instructions(callback: types.CallbackQuery):
    await callback.message.answer(
        "<b>📖 Инструкция по установке:</b>\n\n"
        "1. Скачайте приложение Happ для вашей платформы\n"
        "2. Скопируйте ссылку из раздела «📊 Статус»\n"
        "3. В приложении добавьте подписку:\n"
        "   Тип: Подписка\n"
        "   Имя: любое\n"
        "   URL: вставьте скопированную ссылку\n"
        "4. Подключитесь\n\n"
        f"Подробные инструкции: @{config.SUPPORT_BOT_USERNAME}"
    )
    await callback.answer()


@router.message(F.text == "🎁 Пригласить друга")
async def invite_friend(message: types.Message):
    client = await get_or_create_client(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    await message.answer(
        f"<b>🎁 Пригласите друга - получите {config.REFERRAL_BONUS_DAYS} дней бесплатно!</b>\n\n"
        "Ваш друг получит доступ, а вы - бонусные дни.\n\n"
        "Отправьте другу эту ссылку:",
        reply_markup=referral_keyboard(client.id)
    )


@router.callback_query(F.data.startswith("tariff:"))
async def tariff_selected(callback: types.CallbackQuery):
    tariff_key = callback.data.split(":")[1]
    tariff = config.TARIFFS.get(tariff_key)

    if not tariff:
        await callback.answer("Тариф не найден")
        return

    await callback.message.edit_text(
        f"<b>Выбран тариф: {tariff['name']}</b>\n"
        f"Стоимость: {tariff['price']} руб.\n\n"
        "<b>Оплата через СБП:</b>\n"
        f"Банк: {config.SBP_BANK}\n"
        f"Номер: <code>{config.SBP_PHONE}</code>\n"
        f"Сумма: <b>{tariff['price']} руб.</b>\n\n"
        "После оплаты нажмите «✅ Я оплатил»\n"
        "и пришлите скриншот или последние 4 цифры номера.",
        reply_markup=payment_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "menu:tariffs")
async def show_tariffs(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "<b>Выберите тариф:</b>",
        reply_markup=tariff_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "payment:confirm")
async def payment_confirm(callback: types.CallbackQuery):
    await callback.message.answer(
        "Пришлите скриншот оплаты или последние 4 цифры номера, с которого перевели.\n"
        "Администратор проверит платёж и активирует подписку."
    )
    await callback.answer()


@router.message(F.text, F.text.regexp(r"^\d{4}$"))
async def payment_phone_digits(message: types.Message):
    client = await get_or_create_client(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    async with async_session() as session:
        payment = Payment(
            client_id=client.id,
            amount=0,
            method="sbp",
            phone_last4=message.text,
        )
        session.add(payment)
        await session.commit()

        for admin_id in config.ADMIN_IDS:
            try:
                await message.bot.send_message(
                    admin_id,
                    f"🔔 <b>Новый платёж</b>\n"
                    f"Клиент: @{client.username} (ID: {client.id})\n"
                    f"Последние 4 цифры: <code>{message.text}</code>\n"
                    f"Подтвердить: <code>/confirm {payment.id}</code>"
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить админа {admin_id}: {e}")

    await message.answer(
        "Платёж зарегистрирован. Ожидайте подтверждения.\n"
        f"По вопросам: @{config.SUPPORT_BOT_USERNAME}"
    )


@router.message(F.photo)
async def payment_screenshot(message: types.Message):
    client = await get_or_create_client(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    async with async_session() as session:
        payment = Payment(
            client_id=client.id,
            amount=0,
            method="sbp",
        )
        session.add(payment)
        await session.commit()

        for admin_id in config.ADMIN_IDS:
            try:
                await message.bot.send_photo(
                    admin_id,
                    message.photo[-1].file_id,
                    caption=(
                        f"🔔 <b>Новый платёж (скриншот)</b>\n"
                        f"Клиент: @{client.username} (ID: {client.id})\n"
                        f"Подтвердить: <code>/confirm {payment.id} [сумма]</code>\n"
                        f"Отклонить: <code>/reject {payment.id}</code>"
                    )
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить админа {admin_id}: {e}")

    await message.answer(
        "Скриншот получен. Ожидайте подтверждения.\n"
        f"По вопросам: @{config.SUPPORT_BOT_USERNAME}"
    )


# ========================
# Админские кнопки
# ========================

@router.message(F.text == "⚙️ Админка")
async def admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("<b>Админ-панель</b>", reply_markup=admin_keyboard())


@router.message(F.text == "🔙 Выйти из админки")
async def exit_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Выход из админки.", reply_markup=admin_main_keyboard())


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
        sub = await get_active_subscription(c.id)
        sub_text = f"до {sub.expires_at.strftime('%d.%m')}" if sub else "нет подписки"
        text += f"ID: <code>{c.id}</code> | @{c.username or 'нет'}\n  {c.first_name} | {sub_text}\n"
    await message.answer(text)


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
            select(func.sum(Payment.amount)).where(Payment.status == "confirmed")
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
        from services.xray_api import xray
        data = await xray._api_get("/panel/api/inbounds/list")
        if data and data.get("success"):
            await message.answer(f"<b>🖥 Статус сервера</b>\n\n3x-ui: <b>онлайн</b>\nАдрес: {config.XUI_HOST}")
        else:
            await message.answer(f"<b>🖥 Статус сервера</b>\n\n3x-ui: <b>ошибка подключения</b>\nАдрес: {config.XUI_HOST}")
    except Exception as e:
        await message.answer(f"<b>🖥 Статус сервера</b>\n\n3x-ui: <b>ошибка</b>\n{e}")


@router.message(F.text == "📢 Рассылка")
async def broadcast_start(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Введите сообщение для рассылки всем клиентам.\nДля отмены: /cancel")


@router.message(F.text == "🧹 Очистка подписок")
async def cleanup_subscriptions(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    from sqlalchemy import text
    async with async_session() as session:
        await session.execute(
            text("UPDATE subscriptions SET status = 'expired' WHERE status = 'active' AND expires_at < CURRENT_DATE")
        )
        await session.commit()
    await message.answer("Истекшие подписки очищены.")


@router.message(Command("cancel"))
async def cancel_action(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Действие отменено.", reply_markup=admin_keyboard())


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
