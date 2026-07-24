import logging
from datetime import date, timedelta, datetime

from aiogram import Router, types, F
from aiogram.filters import Command, CommandStart

from sqlalchemy import select, func

from config import config
from database.engine import async_session
from database.models import Client, Subscription, Payment, EventLog, Referral
from services.client_service import (
    get_or_create_client, get_active_subscription,
    is_admin, add_referral_bonus, get_free_sub_link
)
from keyboards.client_kb import (
    main_keyboard, admin_main_keyboard,
    tariff_keyboard, payment_keyboard, status_keyboard,
    help_keyboard, downloads_keyboard, referral_keyboard
)
from keyboards.admin_kb import (
    payment_confirm_keyboard, user_profile_keyboard, admin_keyboard
)

logger = logging.getLogger(__name__)
router = Router()


# ========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ========================

async def get_client_active_subscriptions(client_id: int) -> list:
    """Возвращает список активных подписок клиента."""
    async with async_session() as session:
        result = await session.execute(
            select(Subscription)
            .where(Subscription.client_id == client_id)
            .where(Subscription.status == "active")
            .where(Subscription.expires_at >= date.today())
            .order_by(Subscription.expires_at)
        )
        subs = result.scalars().all()
        
        return [
            {
                "id": sub.id,
                "expires_at": sub.expires_at.strftime('%d.%m.%Y'),
                "plan": sub.plan,
                "is_trial": sub.is_trial,
                "sub_link": sub.sub_link,
            }
            for sub in subs
        ]


async def show_user_profile_by_id(message: types.Message, user_id: int):
    """Показывает профиль пользователя по ID."""
    async with async_session() as session:
        client = await session.get(Client, user_id)
        if not client:
            await message.answer("❌ Клиент не найден.")
            return

        if client.status == "banned":
            await message.answer(f"🚫 Клиент #{user_id} заблокирован.")
            return

        subscriptions = await get_client_active_subscriptions(user_id)
        has_subscription = len(subscriptions) > 0

        text = (
            f"<b>👤 Клиент #{client.id}</b>\n"
            f"<b>Имя:</b> {client.first_name}\n"
            f"<b>Username:</b> @{client.username or 'нет'}\n"
            f"<b>Статус:</b> ✅ активен\n\n"
            f"<b>Активные подписки ({len(subscriptions)}):</b>\n"
        )

        if subscriptions:
            for sub in subscriptions:
                sub_type = "🆓 триал" if sub["is_trial"] else "✅ оплачено"
                text += (
                    f"  • ID {sub['id']} | {sub_type}\n"
                    f"    до {sub['expires_at']} | {sub['plan']}\n"
                )
        else:
            text += "  ❌ нет активных подписок"

        await message.answer(
            text,
            reply_markup=user_profile_keyboard(user_id, has_subscription)
        )


async def show_referrals(message: types.Message):
    """Показывает список рефералов пользователя."""
    client = await get_or_create_client(
        message.chat.id,
        message.from_user.username if message.from_user else None,
        message.from_user.first_name if message.from_user else "Пользователь"
    )

    async with async_session() as session:
        result = await session.execute(
            select(Referral, Client.username, Client.first_name)
            .join(Client, Referral.referred_id == Client.id)
            .where(Referral.referrer_id == client.id)
            .where(Referral.bonus_applied == True)
            .order_by(Referral.referred_paid_at.desc())
        )
        referrals = result.all()
        
        total = await session.scalar(
            select(func.count(Referral.id))
            .where(Referral.referrer_id == client.id)
            .where(Referral.bonus_applied == True)
        )

    if not referrals:
        await message.answer(
            "📭 У вас пока нет рефералов, которые активировали подписку.\n\n"
            "Приглашайте друзей и получайте бонусные дни!\n"
            f"За каждого друга, который оплатит подписку, вы получите +{config.REFERRAL_BONUS_DAYS} дней."
        )
        return

    text = f"<b>🎁 Ваши рефералы ({total or 0})</b>\n\n"
    for ref, username, first_name in referrals:
        name = username or first_name
        date_str = ref.referred_paid_at.strftime('%d.%m.%Y') if ref.referred_paid_at else 'дата неизвестна'
        text += f"• @{name} — оплатил {date_str} (+{ref.bonus_days} дней)\n"
    
    text += f"\n<i>За каждого нового друга вы получаете +{config.REFERRAL_BONUS_DAYS} дней подписки!</i>"

    await message.answer(text)


# ========================
# СТАРТ
# ========================

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    # Логируем входящую команду для отладки
    logger.info(f"=== CMD_START ===")
    logger.info(f"Text: {message.text}")
    logger.info(f"From: {message.from_user.id} (@{message.from_user.username})")
    logger.info(f"Args: {message.text.split()}")
    
    args = message.text.split()
    
    # ========================================
    # 1. ПРОВЕРКА: /start user_{id} (для админов)
    # ========================================
    if len(args) > 1 and args[1].startswith("user_"):
        if not is_admin(message.from_user.id):
            await message.answer("❌ Недостаточно прав.")
            return
        
        try:
            user_id = int(args[1][5:])  # user_123 → 123
        except ValueError:
            await message.answer("❌ Неверный ID пользователя.")
            return
        
        await show_user_profile_by_id(message, user_id)
        return
    
    # ========================================
    # 2. ОДНА СЕССИЯ ДЛЯ ВСЕГО
    # ========================================
    async with async_session() as session:
        # Проверка на бан
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

        # Создаём клиента, если его нет
        if client is None:
            client = Client(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                status="active",
            )
            session.add(client)
            await session.commit()
            await session.refresh(client)
            
            logger.info(f"Новый клиент: {client.id} (@{client.username})")
            
            # Уведомление админов о новом пользователе
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
        else:
            # Если клиент уже существует, обновляем его данные
            client.username = message.from_user.username
            client.first_name = message.from_user.first_name
            await session.commit()

        # ========================================
        # 3. ОБРАБОТКА РЕФЕРАЛЬНОЙ ССЫЛКИ (в той же сессии)
        # ========================================
        ref_arg = None
        if len(args) > 1:
            # Проверяем оба формата: ref_123 и ref123
            if args[1].startswith("ref_"):
                try:
                    ref_id = int(args[1][4:])  # ref_123 → 123
                    if ref_id != client.id:
                        ref_arg = ref_id
                    logger.info(f"Найден реферальный параметр (ref_): {ref_id}")
                except ValueError:
                    logger.warning(f"Неверный формат ref_: {args[1]}")
            elif args[1].startswith("ref"):
                try:
                    ref_id = int(args[1][3:])  # ref123 → 123
                    if ref_id != client.id:
                        ref_arg = ref_id
                    logger.info(f"Найден реферальный параметр (ref): {ref_id}")
                except ValueError:
                    logger.warning(f"Неверный формат ref: {args[1]}")

        # Если есть реферальный параметр и пользователь ещё не привязан
        if ref_arg and client.referrer_id is None:
            referrer = await session.get(Client, ref_arg)
            if referrer:
                client.referrer_id = ref_arg
                client.source = "referral"
                await session.commit()
                
                logger.info(f"Реферал #{client.id} привязан к рефереру #{referrer.id}")
                
                event = EventLog(
                    client_id=client.id,
                    event_type="referral_click",
                    description=f"Переход по реферальной ссылке от {referrer.id}"
                )
                session.add(event)
                await session.commit()
            else:
                logger.warning(f"Реферер с ID {ref_arg} не найден")

    # ========================================
    # 4. ПРИВЕТСТВИЕ (вне сессии)
    # ========================================
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
# АДМИН-ПАНЕЛЬ (через кнопку)
# ========================

@router.callback_query(F.data == "menu:admin")
async def admin_panel_callback(callback: types.CallbackQuery):
    """Вход в админ-панель через кнопку."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    
    await callback.message.answer(
        "<b>⚙️ Админ-панель</b>\n\n"
        "Выберите действие:",
        reply_markup=admin_keyboard()
    )
    await callback.answer()


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

    # Новый формат ссылки с подчёркиванием
    ref_link = f"https://t.me/{config.BOT_USERNAME}?start=ref_{client.id}"

    await message.answer(
        f"<b>🎁 Пригласите друга — получите {config.REFERRAL_BONUS_DAYS} дней бесплатно!</b>\n\n"
        "Ваш друг получит доступ, а вы — бонусные дни.\n\n"
        f"<b>Ваша ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "Или нажмите кнопку ниже, чтобы поделиться:",
        reply_markup=referral_keyboard(client.id)
    )


@router.callback_query(F.data == "menu:referrals")
async def referrals_callback(callback: types.CallbackQuery):
    await show_referrals(callback.message)
    await callback.answer()


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