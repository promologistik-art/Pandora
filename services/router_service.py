import httpx
import logging
from datetime import datetime
from sqlalchemy import select

from config import config
from database.engine import async_session
from database.models import Router

logger = logging.getLogger(__name__)


async def fetch_routers_from_api() -> list | None:
    """Получает список роутеров из API."""
    url = f"{config.ROUTER_API_URL}/api/router/list"
    headers = {
        "Authorization": f"Bearer {config.ROUTER_API_TOKEN}",
        "Accept": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("success"):
                return data.get("routers", [])
            
            logger.error(f"API вернул ошибку: {data}")
            return None
    except Exception as e:
        logger.error(f"Ошибка запроса к API: {e}")
        return None


async def sync_routers() -> dict:
    """Синхронизирует роутеры из API в БД бота."""
    logger.info("🔄 Синхронизация роутеров...")
    
    routers = await fetch_routers_from_api()
    if routers is None:
        logger.error("❌ Не удалось получить список роутеров")
        return {"success": False, "added": 0, "updated": 0, "deleted": 0}
    
    added = 0
    updated = 0
    deleted = 0
    
    # Собираем MAC-адреса из API
    api_macs = set()
    for router_data in routers:
        mac = router_data.get("mac")
        if mac:
            api_macs.add(mac)
    
    async with async_session() as session:
        # 1. Обновляем или добавляем роутеры из API
        for router_data in routers:
            mac = router_data.get("mac")
            email = router_data.get("email")
            last_heartbeat_str = router_data.get("last_heartbeat")
            firmware = router_data.get("firmware_version")
            last_ip = router_data.get("last_ip")
            
            if not mac:
                continue
            
            result = await session.execute(
                select(Router).where(Router.router_uid == mac)
            )
            router = result.scalar_one_or_none()
            
            last_heartbeat = None
            if last_heartbeat_str:
                try:
                    last_heartbeat = datetime.fromisoformat(last_heartbeat_str)
                except:
                    pass
            
            if router:
                router.email = email
                router.firmware_version = firmware
                router.last_ip = last_ip
                router.last_heartbeat = last_heartbeat
                updated += 1
            else:
                router = Router(
                    router_uid=mac,
                    email=email,
                    firmware_version=firmware,
                    last_ip=last_ip,
                    last_heartbeat=last_heartbeat,
                    status="active",
                )
                session.add(router)
                added += 1
        
        # 2. Удаляем роутеры из БД бота, которых нет в API
        result = await session.execute(select(Router))
        db_routers = result.scalars().all()
        
        for db_router in db_routers:
            if db_router.router_uid not in api_macs:
                logger.info(f"🗑️ Удаляем роутер {db_router.router_uid} (нет в API)")
                await session.delete(db_router)
                deleted += 1
        
        await session.commit()
    
    logger.info(f"✅ Синхронизация завершена: добавлено {added}, обновлено {updated}, удалено {deleted}")
    return {"success": True, "added": added, "updated": updated, "deleted": deleted}


async def get_router_by_mac(mac: str) -> Router | None:
    """Получить роутер по MAC из БД бота."""
    async with async_session() as session:
        result = await session.execute(
            select(Router).where(Router.router_uid == mac)
        )
        return result.scalar_one_or_none()


async def get_all_routers() -> list:
    """Получить все роутеры из БД бота."""
    async with async_session() as session:
        result = await session.execute(
            select(Router).order_by(Router.created_at.desc())
        )
        return result.scalars().all()


async def delete_router_from_db(mac: str) -> bool:
    """Удалить роутер из БД бота."""
    async with async_session() as session:
        result = await session.execute(
            select(Router).where(Router.router_uid == mac)
        )
        router = result.scalar_one_or_none()
        
        if not router:
            return False
        
        await session.delete(router)
        await session.commit()
        logger.info(f"🗑️ Роутер {mac} удалён из БД бота")
        return True


def is_router_online(router: Router) -> bool:
    """Проверить, онлайн ли роутер."""
    if not router.last_heartbeat:
        return False
    
    delta = (datetime.utcnow() - router.last_heartbeat).total_seconds()
    return delta < 600  # 10 минут