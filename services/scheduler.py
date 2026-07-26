import logging
import asyncio
from datetime import date, timedelta, datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, func

from config import config
from database.engine import async_session
from database.models import Client, Subscription, Payment, EventLog, TrafficLog
from services.xray_api import xray
from services.client_service import get_active_subscription
from services.cleanup import full_cleanup

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


# ============================================================
# НОВАЯ ЗАДАЧА: ОЧИСТКА ORPHAN-КЛИЕНТОВ
# ============================================================

async def cleanup_orphan_clients():
    """Удаляет клиентов из 3x-ui, у которых нет активной подписки."""
    logger.info("🧹 Начинаем проверку orphan-клиентов в 3x-ui...")
    
    async with async_session() as session:
        result = await session.execute(select(Client))
        clients = result.scalars().all()
        
        removed_count = 0
        for client in clients:
            sub = await get_active_subscription(client.id)
            if not sub and client.xray_uuid:
                try:
                    success = await xray.remove_client(client.xray_uuid)
                    if success:
                        logger.info(f"✅ Удалён orphan-клиент {client.id} (@{client.username}) из 3x-ui")
                        removed_count += 1
                    else:
                        logger.info(f"ℹ️ Клиент {client.id} (@{client.username}) уже удалён из 3x-ui или не найден")
                except Exception as e:
                    logger.error(f"❌ Ошибка удаления клиента {client.id}: {e}")
        
        logger.info(f"🧹 Проверка orphan-клиентов завершена. Удалено: {removed_count}")


# ============================================================
# Задача 1: Сбор трафика за вчерашний день
# ============================================================

async def collect_traffic_with_retry(max_retries: int = 3, delay: int = 10):
    """Собирает трафик клиентов за вчерашний день с повторными попытками."""
    yesterday = date.today() - timedelta(days=1)
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Попытка {attempt+1}/{max_retries} сбора трафика за {yesterday}")
            await collect_traffic()
            logger.info(f"✅ Сбор трафика за {yesterday} успешно завершён")
            return
        except Exception as e:
            logger.warning(f"⚠️ Попытка {attempt+1}/{max_retries} не удалась: {e}")
            if attempt < max_retries - 1:
                logger.info(f"⏳ Повторная попытка через {delay} секунд...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"❌ Не удалось собрать трафик после {max_retries} попыток")


async def collect_traffic():
    """Собирает трафик клиентов за вчерашний день."""
    yesterday = date.today() - timedelta(days=1)
    
    async with async_session() as session:
        result = await session.execute(
            select(Client)
            .join(Subscription, Client.id == Subscription.client_id)
            .where(Subscription.status == "active")
            .where(Subscription.expires_at >= date.today())
            .distinct()
        )
        clients = result.scalars().all()
        
        if not clients:
            logger.info("Нет активных клиентов для сбора трафика")
            return
        
        count = 0
        for client in clients:
            sub = await get_active_subscription(client.id)
            if not sub or not sub.xray_uuid:
                continue
            
            try:
                data = await xray._api_get(
                    f"/panel/api/inbounds/getClient/{config.XUI_INBOUND_ID}/{sub.xray_uuid}"
                )
                if data and data.get("success"):
                    client_data = data.get("obj", {})
                    up = client_data.get("up", 0)
                    down = client_data.get("down", 0)
                    
                    existing = await session.execute(
                        select(TrafficLog)
                        .where(TrafficLog.client_id == client.id)
                        .where(TrafficLog.date == yesterday)
                    )
                    if existing.scalar_one_or_none():
                        await session.execute(
                            TrafficLog.__table__.update()
                            .where(TrafficLog.client_id == client.id)
                            .where(TrafficLog.date == yesterday)
                            .values(upload_bytes=up, download_bytes=down)
                        )
                    else:
                        log = TrafficLog(
                            client_id=client.id,
                            date=yesterday,
                            upload_bytes=up,
                            download_bytes=down,
                        )
                        session.add(log)
                    
                    count += 1
            except Exception as e:
                logger.error(f"Ошибка получения трафика для клиента {client.id}: {e}")
        
        await session.commit()
        logger.info(f"Собраны данные трафика для {count} клиентов за {yesterday}")


# ============================================================
# Задача 2: Напоминания клиентам об истечении подписки
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
# Задача 3: Ежедневная сводка админу
# ============================================================

async def daily_report(bot: Bot):
    today = date.today()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)

    async with async_session() as session:
        new_clients = await session.scalar(
            select(func.count(Client.id))
            .where(func.date(Client.created_at) == yesterday)
        )

        total_clients = await session.scalar(select(func.count(Client.id)))

        active_subs = await session.scalar(
            select(func.count(Subscription.id))
            .where(Subscription.status == "active")
            .where(Subscription.expires_at >= today)
        )

        expired_yesterday = await session.scalar(
            select(func.count(Subscription.id))
            .where(Subscription.status == "expired")
            .where(Subscription.expires_at == yesterday)
        )

        payments_yesterday = await session.scalar(
            select(func.sum(Payment.amount))
            .where(Payment.status == "confirmed")
            .where(func.date(Payment.confirmed_at) == yesterday)
        )

        month_start = today.replace(day=1)
        payments_month = await session.scalar(
            select(func.sum(Payment.amount))
            .where(Payment.status == "confirmed")
            .where(Payment.confirmed_at >= month_start)
        )

        traffic = await session.execute(
            select(
                func.sum(TrafficLog.upload_bytes).label("upload"),
                func.sum(TrafficLog.download_bytes).label("download")
            )
            .where(TrafficLog.date == yesterday)
        )
        traffic_data = traffic.one()
        upload_mb = traffic_data.upload // (1024 * 1024) if traffic_data.upload else 0
        download_mb = traffic_data.download // (1024 * 1024) if traffic_data.download else 0

        expiring_tomorrow = await session.execute(
            select(Subscription, Client.username, Client.first_name)
            .join(Client, Subscription.client_id == Client.id)
            .where(Subscription.status == "active")
            .where(Subscription.expires_at == tomorrow)
        )
        expiring_list = expiring_tomorrow.all()

    report = (
        f"<b>📊 Ежедневная сводка</b>\n"
        f"Дата отчёта: {today.strftime('%d.%m.%Y')}\n"
        f"Данные за: {yesterday.strftime('%d.%m.%Y')}\n\n"
        f"<b>📈 Общие показатели:</b>\n"
        f"Новых клиентов: {new_clients or 0}\n"
        f"Всего клиентов: {total_clients or 0}\n"
        f"Активных подписок: {active_subs or 0}\n"
        f"Истекло: {expired_yesterday or 0}\n\n"
        f"<b>💰 Финансы:</b>\n"
        f"Выручка за день: {payments_yesterday or 0} руб.\n"
        f"Выручка за месяц: {payments_month or 0} руб.\n\n"
        f"<b>📊 Трафик за вчера ({yesterday.strftime('%d.%m')}):</b>\n"
        f"Upload: {upload_mb} MB\n"
        f"Download: {download_mb} MB\n\n"
    )

    if expiring_list:
        report += "<b>⚠️ Истекает завтра:</b>\n"
        for sub, username, first_name in expiring_list:
            name = username or first_name
            plan = "триал" if sub.is_trial else sub.plan
            report += f"@{name} — до {sub.expires_at.strftime('%d.%m')} ({plan})\n"
    else:
        report += "<b>✅ Никто не истекает завтра</b>"

    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report)
        except Exception as e:
            logger.error(f"Не удалось отправить сводку админу {admin_id}: {e}")


# ============================================================
# Задача 4: Мониторинг сервера 3x-ui
# ============================================================

async def monitor_server(bot: Bot):
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
    # 1. Очистка orphan-клиентов — каждый день в 3:30 ночи
    scheduler.add_job(
        cleanup_orphan_clients,
        CronTrigger(hour=3, minute=30),
        id="cleanup_orphan_clients",
        replace_existing=True,
    )

    # 2. Очистка системы — 1-го числа каждого месяца в 2:30 ночи
    scheduler.add_job(
        full_cleanup,
        CronTrigger(day=1, hour=2, minute=30),
        id="system_cleanup",
        replace_existing=True,
    )

    # 3. Сбор трафика — в 3:00 (с повторными попытками)
    scheduler.add_job(
        collect_traffic_with_retry,
        CronTrigger(hour=3, minute=0),
        args=[3, 10],
        id="collect_traffic",
        replace_existing=True,
    )

    # 4. Напоминания об истечении — в 9:00 МСК
    scheduler.add_job(
        check_expiring_subscriptions,
        CronTrigger(hour=5, minute=0),
        args=[bot],
        id="check_expiring",
        replace_existing=True,
    )

    # 5. Ежедневная сводка — в 8:00 МСК
    scheduler.add_job(
        daily_report,
        CronTrigger(hour=4, minute=0),
        args=[bot],
        id="daily_report",
        replace_existing=True,
    )

    # 6. Мониторинг сервера — каждые 30 минут
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