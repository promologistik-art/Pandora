import logging
from datetime import date, timedelta, datetime

from aiogram import Router, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import ReplyKeyboardRemove

from sqlalchemy import select, func

from config import config
from database.engine import async_session
from database.models import Client, Subscription, Payment, EventLog
from services.client_service import (
    get_or_create_client, get_active_subscription,
    is_admin, add_referral_bonus, get_free_sub_link
)
from keyboards.client_kb import (
    main_keyboard, admin_main_keyboard,
    tariff_keyboard, payment_keyboard, status_keyboard,
    help_keyboard, downloads_keyboard, referral_keyboard
)

logger = logging.getLogger(__name__)
router = Router()


# ========================
# СТАРТ
# ========================

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    # Проверяем, не забанен ли пользователь
    async with async_session() as session:
        result = await session.execute(
            select(Client).where(Client.telegram_id == message.from_user.id)
        )
        client = result.scalar_one_or_none()
        if client and client.status == "banned":
            await message.answer(
                "🚫 <b>Ваш доступ заблокирован.</b>\n\n"
                f"Свяжитесь с поддержкой: @{config.SUPPORT_BOT_USERNAME}"
            )
            return

    # Сразу убираем старую Reply-клавиатуру
    await message.answer("", reply_markup=ReplyKeyboardRemove())

    client = await get_or_create_client(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    # Уведомление админов о новом пользователе
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

    # Обработка реферальной ссылки
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
        "<b>📦 Ящик Пандоры</b> — стабильный VPN с умной маршрутизацией.\n"
        "Заблокированные сайты работают, белые списки не тормозят.\n\n"
        "<i>Нет Telegram?</i> Инструкции и поддержка ВКонтакте:\n"
        f"{config.VK_PAGE}"
    )

    if is_admin(message.from_user.id):
        await message.answer(welcome, reply_markup=admin_main_keyboard())
    else:
        await message.answer(welcome, reply_markup=main_keyboard())


# ========================
# ТРИАЛ
# ========================

@router.callback_query(F.data == "menu:trial")
async def trial_start(callback: types.CallbackQuery):
    client = await get_or_create_client(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    if client.status == "banned":
        await callback.message.answer(
            "🚫 <b>Ваш доступ заблокирован.</b>\n\n"
            f"Свяжитесь с поддержкой: @{config.SUPPORT_BOT_USERNAME}"
        )
        await callback.answer()
        return

    async with async_session() as session:
        result = await session.execute(
            select(func.count(Subscription.id))
            .where(Subscription.client_id == client.id)
            .where(Subscription.status == "active")
            .where(Subscription.expires_at >= date.today())
        )
        if result.scalar() > 0:
            await callback.message.answer(
                "✅ У вас уже есть активная подписка.\n"
                "Проверьте статус: кнопка «📊 Статус»"
            )
            await callback.answer()
            return

        sub_link = await get_free_sub_link(session)
        if not sub_link:
            await callback.message.answer(
                "❌ К сожалению, все пробные места сейчас заняты.\n"
                "Попробуйте позже или свяжитесь с поддержкой."
            )
            await callback.answer()
            return

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

    await callback.message.answer(
        f"<b>🎉 Триал-доступ активирован на {config.TRIAL_DAYS} дня!</b>\n\n"
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
    await callback.answer()


# ========================
# СТАТУС
# ========================

@router.callback_query(F.data == "menu:status")
async def status_callback(callback: types.CallbackQuery):
    await show_status(callback.message)
    await callback.answer()


@router.message(Command("status"))
async def cmd_status_command(message: types.Message):
    await show_status(message)


async def show_status(message: types.Message):
    client = await get_or_create_client(
        message.chat.id,
        message.from_user.username if message.from_user else None,
        message.from_user.first_name if message.from_user else "Пользователь"
    )

    if client.status == "banned":
        await message.answer(
            "🚫 <b>Ваш доступ заблокирован.</b>\n\n"
            f"Свяжитесь с поддержкой: @{config.SUPPORT_BOT_USERNAME}"
        )
        return

    sub = await get_active_subscription(client.id)

    if sub is None:
        await message.answer(
            "❌ У вас нет активной подписки.\n"
            "Попробуйте бесплатный триал или выберите тариф:",
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


# ========================
# ПОМОЩЬ
# ========================

@router.callback_query(F.data == "menu:help")
async def help_callback(callback: types.CallbackQuery):
    await show_help(callback.message)
    await callback.answer()


@router.message(Command("help"))
async def cmd_help_command(message: types.Message):
    await show_help(message)


async def show_help(message: types.Message):
    await message.answer(
        "<b>🆘 Помощь и FAQ</b>\n\n"
        "<b>Частые вопросы:</b>\n"
        "• Не работает YouTube — попробуйте переподключиться\n"
        "• Медленная скорость — проверьте сервер в статусе\n"
        "• Как установить на устройство — см. инструкции ниже\n\n"
        f"<b>Поддержка:</b> @{config.SUPPORT_BOT_USERNAME}\n"
        f"<b>ВКонтакте:</b> {config.VK_PAGE}",
        reply_markup=help_keyboard()
    )


# ========================
# ИНСТРУКЦИИ И СКАЧИВАНИЕ
# ========================

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


# ========================
# РЕФЕРАЛЬНАЯ ПРОГРАММА
# ========================

@router.callback_query(F.data == "menu:invite")
async def invite_callback(callback: types.CallbackQuery):
    await show_invite(callback.message)
    await callback.answer()


@router.message(Command("invite"))
async def cmd_invite_command(message: types.Message):
    await show_invite(message)


async def show_invite(message: types.Message):
    client = await get_or_create_client(
        message.chat.id,
        message.from_user.username if message.from_user else None,
        message.from_user.first_name if message.from_user else "Пользователь"
    )

    if client.status == "banned":
        await message.answer(
            "🚫 <b>Ваш доступ заблокирован.</b>\n\n"
            f"Свяжитесь с поддержкой: @{config.SUPPORT_BOT_USERNAME}"
        )
        return

    ref_link = f"https://t.me/{config.BOT_USERNAME}?start=ref{client.id}"

    await message.answer(
        f"<b>🎁 Пригласите друга — получите {config.REFERRAL_BONUS_DAYS} дней бесплатно!</b>\n\n"
        "Ваш друг получит доступ, а вы — бонусные дни.\n\n"
        f"<b>Ваша ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "Или нажмите кнопку ниже, чтобы поделиться:",
        reply_markup=referral_keyboard(client.id)
    )


# ========================
# ТАРИФЫ
# ========================

@router.callback_query(F.data == "menu:tariffs")
async def show_tariffs(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "<b>💰 Выберите тариф:</b>",
        reply_markup=tariff_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tariff:"))
async def tariff_selected(callback: types.CallbackQuery):
    tariff_key = callback.data.split(":")[1]
    tariff = config.TARIFFS.get(tariff_key)

    if not tariff:
        await callback.answer("Тариф не найден")
        return

    await callback.message.edit_text(
        f"<b>✅ Выбран тариф: {tariff['name']}</b>\n"
        f"<b>Стоимость:</b> {tariff['price']} руб.\n\n"
        "<b>💳 Оплата через СБП:</b>\n"
        f"<b>Банк:</b> {config.SBP_BANK}\n"
        f"<b>Номер:</b> <code>{config.SBP_PHONE}</code>\n"
        f"<b>Сумма:</b> {tariff['price']} руб.\n\n"
        "После оплаты нажмите «✅ Я оплатил»\n"
        "и пришлите скриншот или последние 4 цифры номера.",
        reply_markup=payment_keyboard()
    )
    await callback.answer()


# ========================
# ОПЛАТА
# ========================

@router.callback_query(F.data == "payment:confirm")
async def payment_confirm(callback: types.CallbackQuery):
    client = await get_or_create_client(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    if client.status == "banned":
        await callback.message.answer(
            "🚫 <b>Ваш доступ заблокирован.</b>\n\n"
            f"Свяжитесь с поддержкой: @{config.SUPPORT_BOT_USERNAME}"
        )
        await callback.answer()
        return

    await callback.message.answer(
        "📝 Пришлите скриншот оплаты или последние 4 цифры номера, с которого перевели.\n"
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

    if client.status == "banned":
        await message.answer(
            "🚫 <b>Ваш доступ заблокирован.</b>\n\n"
            f"Свяжитесь с поддержкой: @{config.SUPPORT_BOT_USERNAME}"
        )
        return

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
                    f"Последние 4 цифры: <code>{message.text}</code>",
                    reply_markup=payment_confirm_keyboard(payment.id)
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить админа {admin_id}: {e}")

    await message.answer(
        "✅ Платёж зарегистрирован. Ожидайте подтверждения.\n"
        f"По вопросам: @{config.SUPPORT_BOT_USERNAME}"
    )


@router.message(F.photo)
async def payment_screenshot(message: types.Message):
    client = await get_or_create_client(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    if client.status == "banned":
        await message.answer(
            "🚫 <b>Ваш доступ заблокирован.</b>\n\n"
            f"Свяжитесь с поддержкой: @{config.SUPPORT_BOT_USERNAME}"
        )
        return

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
                        f"Клиент: @{client.username} (ID: {client.id})"
                    ),
                    reply_markup=payment_confirm_keyboard(payment.id)
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить админа {admin_id}: {e}")

    await message.answer(
        "✅ Скриншот получен. Ожидайте подтверждения.\n"
        f"По вопросам: @{config.SUPPORT_BOT_USERNAME}"
    )