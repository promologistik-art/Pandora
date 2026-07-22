import logging
from datetime import date, timedelta

from aiogram import Router, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import ReplyKeyboardRemove

from sqlalchemy import select

from config import config
from database.engine import async_session
from database.models import Client, Subscription, Payment, EventLog
from services.xray_api import xray
from keyboards.client_kb import (
    main_keyboard, tariff_keyboard, payment_keyboard,
    status_keyboard, help_keyboard, downloads_keyboard,
    referral_keyboard
)

logger = logging.getLogger(__name__)
router = Router()


# === Вспомогательные функции ===

async def get_or_create_client(telegram_id: int, username: str, first_name: str) -> Client:
    """Возвращает существующего клиента или создаёт нового."""
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

            # Логируем
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
    """Возвращает активную подписку клиента."""
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


def build_vless_link(uuid: str, host: str) -> str:
    """Собирает ссылку VLESS для клиента."""
    # Упрощённая версия: vless://uuid@host:443?encryption=none&security=reality&type=tcp&flow=xtls-rprx-vision#Pandora
    return f"vless://{uuid}@{host}:443?encryption=none&security=reality&type=tcp&flow=xtls-rprx-vision#Pandora"


# === Команда /start ===

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    """Приветствие и главное меню."""
    client = await get_or_create_client(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    # Проверяем реферальную ссылку
    ref_arg = None
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref"):
        try:
            ref_id = int(args[1][3:])
            if ref_id != client.id:
                ref_arg = ref_id
        except ValueError:
            pass

    # Обрабатываем реферала
    if ref_arg and client.referrer_id is None:
        async with async_session() as session:
            referrer = await session.get(Client, ref_arg)
            if referrer:
                client.referrer_id = ref_arg
                client.source = "referral"
                await session.commit()

                # Бонус рефереру
                await add_referral_bonus(referrer, session)

    welcome = (
        "<b>Ящик Пандоры</b> - стабильный VPN с умной маршрутизацией.\n"
        "Заблокированные сайты работают, белые списки не тормозят.\n\n"
        "<i>Нет Telegram?</i> Инструкции и поддержка ВКонтакте:\n"
        f"{config.VK_PAGE}"
    )
    await message.answer(welcome, reply_markup=main_keyboard())


async def add_referral_bonus(referrer: Client, session):
    """Начисляет бонусные дни рефереру."""
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


# === Кнопка "💳 Попробовать 3 дня бесплатно" ===

@router.message(F.text == "💳 Попробовать 3 дня бесплатно")
async def trial_start(message: types.Message):
    """Активация триала."""
    client = await get_or_create_client(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    # Проверяем, был ли уже триал
    async with async_session() as session:
        result = await session.execute(
            select(Subscription)
            .where(Subscription.client_id == client.id)
            .where(Subscription.is_trial == True)
        )
        if result.scalar_one_or_none():
            await message.answer(
                "Вы уже использовали триал-доступ.\n"
                "Выберите тариф для продолжения:",
                reply_markup=tariff_keyboard()
            )
            return

    # Создаём триал-подписку
    trial_uuid = Subscription.generate_uuid()

    # Добавляем клиента в 3x-ui
    result = await xray.add_client(
        email=f"trial_{client.id}",
        uuid=trial_uuid
    )

    if result is None:
        await message.answer(
            "Произошла ошибка при создании ключа. Попробуйте позже или свяжитесь с поддержкой.",
            reply_markup=payment_keyboard()
        )
        return

    async with async_session() as session:
        sub = Subscription(
            client_id=client.id,
            started_at=date.today(),
            expires_at=date.today() + timedelta(days=config.TRIAL_DAYS),
            plan="trial",
            is_trial=True,
            xray_uuid=trial_uuid,
        )
        session.add(sub)

        event = EventLog(
            client_id=client.id,
            event_type="trial_activated",
            description=f"Триал на {config.TRIAL_DAYS} дн."
        )
        session.add(event)
        await session.commit()

    host = config.XUI_HOST.split("://")[1].split(":")[0] if "://" in config.XUI_HOST else config.XUI_HOST
    vless_link = build_vless_link(trial_uuid, host)

    await message.answer(
        f"<b>Триал-доступ активирован на {config.TRIAL_DAYS} дня!</b>\n\n"
        f"<b>Ваш ключ:</b>\n"
        f"<code>{vless_link}</code>\n\n"
        "<b>Как подключиться:</b>\n"
        "1. Скачайте приложение (кнопка «🆘 Помощь / FAQ»)\n"
        "2. Импортируйте ключ в приложение\n"
        "3. Готово!\n\n"
        f"<b>Поддержка:</b> @{config.SUPPORT_BOT_USERNAME}",
        reply_markup=status_keyboard()
    )


# === Кнопка "📊 Статус" ===

@router.message(F.text == "📊 Статус")
async def cmd_status(message: types.Message):
    """Проверка статуса подписки."""
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
    host = config.XUI_HOST.split("://")[1].split(":")[0] if "://" in config.XUI_HOST else config.XUI_HOST
    vless_link = build_vless_link(sub.xray_uuid, host)

    trial_text = " (триал)" if sub.is_trial else ""

    await message.answer(
        "<b>📊 Статус подписки</b>\n\n"
        f"<b>Статус:</b> активна{trial_text}\n"
        f"<b>Тариф:</b> {config.TARIFFS.get(sub.plan, {}).get('name', sub.plan)}\n"
        f"<b>Действует до:</b> {sub.expires_at.strftime('%d.%m.%Y')}\n"
        f"<b>Осталось дней:</b> {days_left}\n\n"
        "<b>Ваш ключ:</b>\n"
        f"<code>{vless_link}</code>\n\n"
        f"<b>Поддержка:</b> @{config.SUPPORT_BOT_USERNAME}",
        reply_markup=status_keyboard()
    )


# === Кнопка "🆘 Помощь / FAQ" ===

@router.message(F.text == "🆘 Помощь / FAQ")
async def cmd_help(message: types.Message):
    """Помощь и FAQ."""
    await message.answer(
        "<b>🆘 Помощь и FAQ</b>\n\n"
        "<b>Частые вопросы:</b>\n"
        "• Не работает YouTube - попробуйте переподключиться\n"
        "• Медленная скорость - проверьте сервер в статусе\n"
        "• Как установить на устройство - см. инструкции ниже\n\n"
        f"<b>Поддержка:</b> @{config.SUPPORT_BOT_USERNAME}\n"
        f"<b>ВКонтакте:</b> {config.VK_PAGE}",
        reply_markup=help_keyboard()
    )


@router.callback_query(F.data == "help:downloads")
async def show_downloads(callback: types.CallbackQuery):
    """Ссылки на скачивание."""
    await callback.message.edit_text(
        "<b>📥 Скачать приложения:</b>\n\n"
        "Выберите платформу:",
        reply_markup=downloads_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("download:"))
async def send_download_link(callback: types.CallbackQuery):
    """Отправляет ссылку на скачивание."""
    platform = callback.data.split(":")[1]

    links = {
        "windows": "https://github.com/XTLS/Xray-core/releases (Windows)",
        "macos": "https://apps.apple.com/app/streisand/id... (macOS)",
        "android": "https://play.google.com/store/apps/details?id=com.v2ray.ang (Android)",
        "ios": "https://apps.apple.com/app/streisand/id... (iOS)",
        "androidtv": "https://play.google.com/store/apps/details?id=com.v2ray.ang (Android TV)",
    }

    text = links.get(platform, "Ссылка появится позже")
    await callback.message.answer(f"<b>Скачать для {platform}:</b>\n{text}")
    await callback.answer()


@router.callback_query(F.data == "help:instructions")
async def send_instructions(callback: types.CallbackQuery):
    """Отправляет инструкцию."""
    await callback.message.answer(
        "<b>📖 Инструкция по установке:</b>\n\n"
        "1. Скачайте приложение для вашей платформы\n"
        "2. Скопируйте ключ из раздела «📊 Статус»\n"
        "3. Импортируйте ключ в приложение\n"
        "4. Подключитесь\n\n"
        f"Подробные инструкции: @{config.SUPPORT_BOT_USERNAME}"
    )
    await callback.answer()


# === Кнопка "🎁 Пригласить друга" ===

@router.message(F.text == "🎁 Пригласить друга")
async def invite_friend(message: types.Message):
    """Реферальная программа."""
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


# === Обработка тарифов ===

@router.callback_query(F.data.startswith("tariff:"))
async def tariff_selected(callback: types.CallbackQuery):
    """Пользователь выбрал тариф."""
    tariff_key = callback.data.split(":")[1]
    tariff = config.TARIFFS.get(tariff_key)

    if not tariff:
        await callback.answer("Тариф не найден")
        return

    await callback.message.edit_text(
        f"<b>Выбран тариф: {tariff['name']}</b>\n"
        f"Стоимость: {tariff['price']}₽\n\n"
        "<b>Оплата через СБП:</b>\n"
        f"Банк: {config.SBP_BANK}\n"
        f"Номер: <code>{config.SBP_PHONE}</code>\n"
        f"Сумма: <b>{tariff['price']}₽</b>\n\n"
        "После оплаты нажмите «✅ Я оплатил»\n"
        "и пришлите скриншот или последние 4 цифры номера.",
        reply_markup=payment_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "menu:tariffs")
async def show_tariffs(callback: types.CallbackQuery):
    """Показывает тарифы из меню."""
    await callback.message.edit_text(
        "<b>Выберите тариф:</b>",
        reply_markup=tariff_keyboard()
    )
    await callback.answer()


# === Обработка оплаты ===

@router.callback_query(F.data == "payment:confirm")
async def payment_confirm(callback: types.CallbackQuery):
    """Клиент нажал «Я оплатил»."""
    await callback.message.answer(
        "Пришлите скриншот оплаты или последние 4 цифры номера, с которого перевели.\n"
        "Администратор проверит платёж и активирует подписку."
    )
    await callback.answer()


@router.message(F.text, F.text.regexp(r"^\d{4}$"))
async def payment_phone_digits(message: types.Message):
    """Клиент прислал 4 цифры."""
    client = await get_or_create_client(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    # Создаём запись о платеже
    async with async_session() as session:
        payment = Payment(
            client_id=client.id,
            amount=0,  # Будет заполнено админом
            method="sbp",
            phone_last4=message.text,
        )
        session.add(payment)
        await session.commit()

        # Уведомление админам
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
    """Клиент прислал скриншот."""
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