import httpx
import logging
import secrets
import json
import uuid
import asyncio
from datetime import datetime, timedelta
from config import config

logger = logging.getLogger(__name__)


class XRayAPI:
    def __init__(self):
        self.base_url = config.XUI_HOST.rstrip("/")
        self.api_token = config.XUI_API_TOKEN
        self._session: httpx.AsyncClient | None = None

    async def _get_session(self) -> httpx.AsyncClient:
        if self._session is None:
            self._session = httpx.AsyncClient(verify=False, timeout=30.0)
        return self._session

    async def _api_get(self, path: str) -> dict | None:
        if not self.api_token:
            logger.error("3x-ui: API-токен не настроен!")
            return None

        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json"
        }
        try:
            url = f"{self.base_url}{path}"
            logger.info(f"3x-ui GET: {url}")
            resp = await session.get(url, headers=headers)
            logger.info(f"3x-ui Response: {resp.status_code}")
            
            if resp.status_code in (403, 401, 404):
                logger.error(f"3x-ui: ошибка {resp.status_code}")
                return None

            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"3x-ui: ошибка GET {path} - {e}")
            return None

    async def _api_post(self, path: str, json_data: dict = None) -> dict | None:
        if not self.api_token:
            logger.error("3x-ui: API-токен не настроен!")
            return None

        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        try:
            url = f"{self.base_url}{path}"
            logger.info(f"3x-ui POST: {url}")
            resp = await session.post(url, json=json_data or {}, headers=headers)
            logger.info(f"3x-ui Response: {resp.status_code}")

            if resp.status_code in (403, 401, 404):
                logger.error(f"3x-ui: ошибка {resp.status_code}")
                return None

            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"3x-ui: ошибка POST {path} - {e}")
            return None

    async def check_health(self) -> bool:
        try:
            data = await self._api_get("/panel/api/inbounds/list")
            return data and data.get("success")
        except:
            return False

    async def _get_inbounds(self) -> list:
        """Получить список всех inbound'ов."""
        data = await self._api_get("/panel/api/inbounds/list")
        if not data or not data.get("success"):
            return []
        return data.get("obj", [])

    async def _get_active_inbounds(self) -> list:
        """Получить список активных inbound'ов."""
        inbounds = await self._get_inbounds()
        return [inb for inb in inbounds if inb.get("enable", False)]

    async def _get_client_uuid_by_email(self, email: str) -> str | None:
        """Получить UUID клиента по email из settings.clients любого inbound."""
        inbounds = await self._get_inbounds()
        for inbound in inbounds:
            settings = inbound.get("settings", {})
            if isinstance(settings, str):
                settings = json.loads(settings)
            
            for client in settings.get("clients", []):
                if client.get("email") == email:
                    return client.get("id")
        
        return None

    async def add_client(self, email: str, expiry_days: int = 30) -> dict | None:
        """Создаёт клиента и привязывает ко всем активным inbound'ам."""
        logger.info(f"3x-ui: создание клиента {email}, срок {expiry_days} дней")
        
        # 1. Получаем все активные inbound'ы
        active_inbounds = await self._get_active_inbounds()
        if not active_inbounds:
            logger.error("3x-ui: нет активных inbound'ов")
            return None
        
        logger.info(f"3x-ui: найдено {len(active_inbounds)} активных inbound'ов")
        
        # 2. Генерируем UUID один раз для клиента
        new_uuid = str(uuid.uuid4())
        auth = secrets.token_hex(8)
        expiry_time = int((datetime.utcnow() + timedelta(days=expiry_days)).timestamp() * 1000)
        
        success_count = 0
        
        # 3. Добавляем клиента в КАЖДЫЙ inbound (привязываем inbound'ы к клиенту)
        for inbound in active_inbounds:
            inbound_id = inbound.get("id")
            remark = inbound.get("remark", inbound_id)
            logger.info(f"3x-ui: привязываем клиента к inbound {inbound_id} ({remark})")
            
            settings = inbound.get("settings", {})
            if isinstance(settings, str):
                settings = json.loads(settings)
            
            if "clients" not in settings:
                settings["clients"] = []
            
            clients = settings.get("clients", [])
            
            # Проверяем, есть ли уже клиент в этом inbound'е
            if any(c.get("email") == email for c in clients):
                logger.info(f"3x-ui: клиент {email} уже есть в inbound {inbound_id}, пропускаем")
                continue
            
            # Добавляем клиента в этот inbound
            client_data = {
                "id": new_uuid,
                "email": email,
                "enable": True,
                "auth": auth,
                "password": auth,
                "subId": email,
                "limitIp": 3,
                "totalGB": 0,
                "expiryTime": expiry_time,
                "tgId": 0,
                "security": "auto",
                "reset": 0,
            }
            clients.append(client_data)
            settings["clients"] = clients
            
            update_data = {
                "id": inbound_id,
                "protocol": inbound.get("protocol"),
                "port": inbound.get("port"),
                "listen": inbound.get("listen", ""),
                "remark": inbound.get("remark", ""),
                "enable": inbound.get("enable", True),
                "expiryTime": inbound.get("expiryTime", 0),
                "total": inbound.get("total", 0),
                "trafficReset": inbound.get("trafficReset", "never"),
                "settings": settings,
                "streamSettings": inbound.get("streamSettings", {}),
                "sniffing": inbound.get("sniffing", {"enabled": False}),
                "tag": inbound.get("tag", ""),
                "shareAddrStrategy": inbound.get("shareAddrStrategy", "listen"),
                "shareAddr": inbound.get("shareAddr", ""),
                "subSortIndex": inbound.get("subSortIndex", 1),
                "originNodeGuid": inbound.get("originNodeGuid", ""),
            }
            
            result = await self._api_post(
                f"/panel/api/inbounds/update/{inbound_id}",
                update_data
            )
            
            if result and result.get("success"):
                success_count += 1
                logger.info(f"3x-ui: ✅ клиент {email} привязан к inbound {inbound_id}")
            else:
                logger.error(f"3x-ui: ❌ ошибка привязки к inbound {inbound_id}")
        
        if success_count > 0:
            # 4. Ждём 2 секунды, чтобы 3x-ui обработал изменения
            await asyncio.sleep(2)
            
            # 5. Обновляем срок через updateClient для всех inbound'ов
            await self.update_client_expiry(email, expiry_days)
            logger.info(f"3x-ui: срок {expiry_days} дней установлен для {email}")
            
            return {
                "uuid": new_uuid,
                "email": email,
                "auth": auth,
            }
        
        logger.error(f"3x-ui: не удалось привязать клиента ни к одному inbound'у")
        return None

    async def update_client_expiry(self, email: str, expiry_days: int) -> bool:
        """Обновляет срок клиента во всех inbound'ах, где он есть."""
        logger.info(f"3x-ui: обновление срока для {email} до {expiry_days} дней")
        
        client_uuid = await self._get_client_uuid_by_email(email)
        if not client_uuid:
            logger.error(f"3x-ui: клиент {email} не найден")
            return False
        
        expiry_time = int((datetime.utcnow() + timedelta(days=expiry_days)).timestamp() * 1000)
        update_data = {
            "id": client_uuid,
            "email": email,
            "enable": True,
            "expiryTime": expiry_time,
            "limitIp": 3,
            "totalGB": 0,
        }
        
        # Обновляем во всех inbound'ах, где есть клиент
        inbounds = await self._get_inbounds()
        success_count = 0
        
        for inbound in inbounds:
            settings = inbound.get("settings", {})
            if isinstance(settings, str):
                settings = json.loads(settings)
            
            # Проверяем, есть ли клиент в этом inbound'е
            if not any(c.get("email") == email for c in settings.get("clients", [])):
                continue
            
            inbound_id = inbound.get("id")
            result = await self._api_post(
                f"/panel/api/inbounds/updateClient/{inbound_id}/{client_uuid}",
                update_data
            )
            
            if result and result.get("success"):
                success_count += 1
                logger.info(f"3x-ui: ✅ срок для {email} обновлён в inbound {inbound_id}")
            else:
                logger.error(f"3x-ui: ❌ ошибка обновления в inbound {inbound_id}")
        
        return success_count > 0

    async def get_client_link(self, email: str) -> str | None:
        try:
            if config.SUB_LINKS and len(config.SUB_LINKS) > 0:
                template = config.SUB_LINKS[0]
                base = "/".join(template.split("/")[:-1])
                return f"{base}/{email}"
            
            # БЕРЁМ ДОМЕН ИЗ XUI_HOST
            from urllib.parse import urlparse
            parsed = urlparse(config.XUI_HOST)
            domain = parsed.netloc.split(":")[0]
            return f"https://{domain}:2096/sub/{email}"
        except Exception as e:
            logger.error(f"3x-ui: ошибка генерации ссылки - {e}")
            return None

    async def remove_client(self, uuid: str) -> bool:
        """Удаляет клиента из всех inbound'ов."""
        inbounds = await self._get_inbounds()
        success_count = 0
        
        for inbound in inbounds:
            inbound_id = inbound.get("id")
            settings = inbound.get("settings", {})
            if isinstance(settings, str):
                settings = json.loads(settings)
            
            clients = settings.get("clients", [])
            new_clients = [c for c in clients if c.get("id") != uuid]
            
            if len(new_clients) == len(clients):
                continue
            
            settings["clients"] = new_clients
            
            update_data = {
                "id": inbound_id,
                "protocol": inbound.get("protocol"),
                "port": inbound.get("port"),
                "listen": inbound.get("listen", ""),
                "remark": inbound.get("remark", ""),
                "enable": inbound.get("enable", True),
                "expiryTime": inbound.get("expiryTime", 0),
                "total": inbound.get("total", 0),
                "trafficReset": inbound.get("trafficReset", "never"),
                "settings": settings,
                "streamSettings": inbound.get("streamSettings", {}),
                "sniffing": inbound.get("sniffing", {"enabled": False}),
                "tag": inbound.get("tag", ""),
                "shareAddrStrategy": inbound.get("shareAddrStrategy", "listen"),
                "shareAddr": inbound.get("shareAddr", ""),
                "subSortIndex": inbound.get("subSortIndex", 1),
                "originNodeGuid": inbound.get("originNodeGuid", ""),
            }
            
            result = await self._api_post(
                f"/panel/api/inbounds/update/{inbound_id}",
                update_data
            )
            
            if result and result.get("success"):
                success_count += 1
                logger.info(f"3x-ui: ✅ клиент удалён из inbound {inbound_id}")
            else:
                logger.error(f"3x-ui: ❌ ошибка удаления из inbound {inbound_id}")
        
        return success_count > 0

    async def close(self):
        if self._session:
            await self._session.aclose()
            self._session = None


xray = XRayAPI()