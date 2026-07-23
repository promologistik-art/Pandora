import logging
from datetime import date, timedelta, datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, func

from config import config
from database.engine import async_session
from database.models import Client, Subscription, Payment, EventLog
from services.xray_api import xray

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


# ============================================================
# Задача 1: Напоминания клиентам об истечении подписки
# ============================================================

async def check_expiring_subscriptions(bot: Bot):
    today = date.today()
    async with async_session() as session:
        # Подписки, истекающие через 3 дня
        expires_3d = today + timedelta(days=3)
        result = await session.execute(
            select(Subscription)
            .where(Subscription.status == "active")
            .where(Subscription.expires_at == expires_3d)
            .where(Subscription.is_trial == False)
        )
        subs_3d = result.scalars().all()

        for sub in subs_3d:
            client = await session.get(Client, sub.client_id)
            if client:
                try:
                    await bot.send_message(
                        client.telegram_id,
                        "<b>⏰ Ваша подписка истекает через 3 дня.</b>\n"
                        "Продлите, чтобы не потерять доступ.\n"
                        "Используйте кнопку «💳 Продлить» в статусе."
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить напоминание клиенту {client.id}: {e}")

        # Подписки, истекающие сегодня
        result = await session.execute(
            select(Subscription)
            .where(Subscription.status == "active")
            .where(Subscription.expires_at == today)
        )
        subs_today = result.scalars().all()

        for sub in subs_today:
            client = await session.get(Client, sub.client_id)
            if client:
                sub.status = "expired"
                await session.commit()

                await xray.remove_client(sub.xray_uuid)

                event = EventLog(
                    client_id=client.id,
                    event_type="subscription_expired",
                    description=f"Подписка истекла {today}"
                )
                session.add(event)
                await session.commit()

                try:
                    await bot.send_message(
                        client.telegram_id,
                        "<b>❌ Подписка истекла.</b>\n"
                        "Доступ приостановлен.\n"
                        "Оплатите, чтобы возобновить."
                    )
                except Exception as e:
                    logger.warning(f"Не удалось уведомить клиента {client.id}: {e}")

        # Триалы, истекающие завтра
        expires_tomorrow = today + timedelta(days=1)
        result = await session.execute(
            select(Subscription)
            .where(Subscription.status == "active")
            .where(Subscription.expires_at == expires_tomorrow)
            .where(Subscription.is_trial == True)
        )
        trials = result.scalars().all()

        for sub in trials:
            client = await session.get(Client, sub.client_id)
            if client:
                try:
                    await bot.send_message(
                        client.telegram_id,
                        "<b>⏰ Триал заканчивается завтра.</b>\n"
                        "Выберите тариф, чтобы продолжить пользоваться VPN."
                    )
                except Exception as e:
                    logger.warning(f"Не удалось уведомить клиента {client.id}: {e}")


# ============================================================
# Задача 2: Ежедневная сводка админу
# ============================================================

async def daily_report(bot: Bot):
    today = date.today()

    async with async_session() as session:
        new_clients = await session.scalar(
            select(func.count(Client.id))
            .where(func.date(Client.created_at) == today)
        )

        payments_today = await session.scalar(
            select(func.sum(Payment.amount))
            .where(Payment.status == "confirmed")
            .where(func.date(Payment.confirmed_at) == today)
        )

        active_subs = await session.scalar(
            select(func.count(Subscription.id))
            .where(Subscription.status == "active")
            .where(Subscription.expires_at >= today)
        )

        expired_today = await session.scalar(
            select(func.count(Subscription.id))
            .where(Subscription.status == "expired")
            .where(Subscription.expires_at == today)
        )

        total_clients = await session.scalar(select(func.count(Client.id)))

    report = (
        "<b>📊 Ежедневная сводка</b>\n"
        f"Дата: {today.strftime('%d.%m.%Y')}\n\n"
        f"<b>Новых клиентов:</b> {new_clients or 0}\n"
        f"<b>Выручка за сегодня:</b> {payments_today or 0} руб.\n"
        f"<b>Активных подписок:</b> {active_subs or 0}\n"
        f"<b>Истекло сегодня:</b> {expired_today or 0}\n"
        f"<b>Всего клиентов:</b> {total_clients or 0}"
    )

    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report)
        except Exception as e:
            logger.error(f"Не удалось отправить сводку админу {admin_id}: {e}")


# ============================================================
# Задача 3: Мониторинг сервера 3x-ui
# ============================================================

async def monitor_server(bot: Bot):
    """Проверяет доступность 3x-ui и алертит админов при падении."""
    try:
        if await xray.check_health():
            logger.info("Мониторинг сервера: 3x-ui онлайн")
        else:
            for admin_id in config.ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"⚠️ <b>Сервер 3x-ui недоступен!</b>\n"
                        f"Адрес: {config.XUI_HOST}"
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить алерт админу {admin_id}: {e}")
    except Exception as e:
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ <b>Сервер 3x-ui недоступен!</b>\n"
                    f"Ошибка: {e}"
                )
            except Exception as ex:
                logger.error(f"Не удалось отправить алерт админу {admin_id}: {ex}")


# ============================================================
# Запуск планировщика
# ============================================================

async def start_scheduler(bot: Bot):
    scheduler.add_job(
        check_expiring_subscriptions,
        CronTrigger(hour=10, minute=0),
        args=[bot],
        id="check_expiring",
        replace_existing=True,
    )

    scheduler.add_job(
        daily_report,
        CronTrigger(hour=9, minute=0),
        args=[bot],
        id="daily_report",
        replace_existing=True,
    )

    scheduler.add_job(
        monitor_server,
        IntervalTrigger(minutes=30),
        args=[bot],
        id="monitor_server",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Планировщик запущен")


async def stop_scheduler():
    scheduler.shutdown()
    logger.info("Планировщик остановлен")