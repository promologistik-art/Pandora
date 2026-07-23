import logging
from datetime import date, timedelta, datetime

from sqlalchemy import select, func

from config import config
from database.engine import async_session
from database.models import Client, Subscription, Payment, EventLog

logger = logging.getLogger(__name__)


async def get_or_create_client(telegram_id: int, username: str, first_name: str) -> Client:
    """Получить или создать клиента."""
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
    """Получить активную подписку клиента."""
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
    """Начислить бонусные дни за реферала."""
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
    """Проверить, является ли пользователь админом."""
    return user_id in config.ADMIN_IDS


async def get_free_sub_link(session) -> str | None:
    """Получить свободную ссылку подписки из пула."""
    used_links = await session.execute(
        select(Subscription.sub_link)
        .where(Subscription.status == "active")
        .where(Subscription.expires_at >= date.today())
    )
    used = set(row[0] for row in used_links if row[0])
    free_links = [link for link in config.SUB_LINKS if link not in used]
    return free_links[0] if free_links else None