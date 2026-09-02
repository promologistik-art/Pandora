import httpx
import logging
import re
import json
import asyncio
import secrets
import string
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse
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

    def _generate_auth(self, length: int = 16) -> str:
        """Генерирует случайную строку для Hysteria Auth."""
        alphabet = string.ascii_lowercase + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))

    def _generate_password(self, length: int = 16) -> str:
        """Генерирует случайный пароль."""
        alphabet = string.ascii_lowercase + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))

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
            logger.info(f"3x-ui Payload: {json.dumps(json_data or {}, indent=2)[:1000]}")
            resp = await session.post(url, json=json_data or {}, headers=headers)
            logger.info(f"3x-ui Response: {resp.status_code}")
            logger.info(f"3x-ui Response body: {resp.text[:500]}")

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
        active = [inb for inb in inbounds if inb.get("enable", False)]
        logger.info(f"3x-ui: активные inbound'ы: {[inb.get('id') for inb in active]}")
        return active

    async def _get_client_by_email(self, email: str) -> dict | None:
        """Получить клиента по email через /clients/get/{email}."""
        result = await self._api_get(f"/panel/api/clients/get/{email}")
        if result and result.get("success"):
            return result.get("obj")
        return None

    async def add_client(self, email: str, expiry_days: int = 30) -> dict | None:
        """
        Создаёт клиента через /clients/add с указанием inboundIds.
        Генерирует все необходимые поля на стороне бота.
        """
        logger.info(f"3x-ui: создание клиента {email}, срок {expiry_days} дней")
        
        active_inbounds = await self._get_active_inbounds()
        if not active_inbounds:
            logger.error("3x-ui: нет активных inbound'ов")
            return None
        
        inbound_ids = [inb.get("id") for inb in active_inbounds]
        logger.info(f"3x-ui: привязываем клиента к inbound'ам: {inbound_ids}")
        
        client_uuid = str(uuid.uuid4())
        client_password = self._generate_password()
        client_auth = self._generate_auth()
        
        expiry_time = int((datetime.utcnow() + timedelta(days=expiry_days)).timestamp() * 1000)
        logger.info(f"3x-ui: expiryTime = {expiry_time} ({expiry_days} дней)")
        
        client_payload = {
            "email": email,
            "subId": email,
            "id": client_uuid,
            "password": client_password,
            "auth": client_auth,
            "enable": True,
            "expiryTime": expiry_time,
            "limitIp": config.LIMIT_IP,  # ✅ 5 устройств
            "totalGB": 0,
            "comment": "",
            "group": "",
            "limitHwid": 0,
            "reset": 0,
            "resetDay": 0,
            "resetMax": 0,
            "security": "auto",
            "tgId": 0,
            "flow": "",
            "trafficReset": "never",
            "trafficResetDay": 1,
        }
        
        payload = {
            "client": client_payload,
            "inboundIds": inbound_ids,
        }
        
        result = await self._api_post("/panel/api/clients/add", payload)
        
        if result and result.get("success"):
            logger.info(f"3x-ui: ✅ клиент {email} создан с UUID {client_uuid}")
            return {
                "uuid": client_uuid,
                "email": email,
                "password": client_password,
                "auth": client_auth,
            }
        
        logger.error(f"3x-ui: ❌ ошибка создания клиента: {result}")
        return None

    async def update_client_expiry(self, email: str, expiry_days: int) -> bool:
        """Обновляет срок клиента через /clients/update/{email}."""
        logger.info(f"3x-ui: обновление срока для {email} до {expiry_days} дней")
        
        client = await self._get_client_by_email(email)
        if not client:
            logger.error(f"3x-ui: клиент {email} не найден")
            return False
        
        expiry_time = int((datetime.utcnow() + timedelta(days=expiry_days)).timestamp() * 1000)
        
        payload = {
            "email": email,
            "subId": client.get("subId", email),
            "id": client.get("id"),
            "password": client.get("password", ""),
            "auth": client.get("auth", ""),
            "enable": client.get("enable", True),
            "expiryTime": expiry_time,
            "limitIp": config.LIMIT_IP,  # ✅ 5 устройств
            "totalGB": client.get("totalGB", 0),
            "comment": client.get("comment", ""),
            "group": client.get("group", ""),
            "limitHwid": client.get("limitHwid", 0),
            "reset": client.get("reset", 0),
            "resetDay": client.get("resetDay", 0),
            "resetMax": client.get("resetMax", 0),
            "security": client.get("security", "auto"),
            "tgId": client.get("tgId", 0),
            "flow": client.get("flow", ""),
            "trafficReset": client.get("trafficReset", "never"),
            "trafficResetDay": client.get("trafficResetDay", 1),
            "inboundIds": client.get("inboundIds", []),
        }
        
        result = await self._api_post(f"/panel/api/clients/update/{email}", payload)
        
        if result and result.get("success"):
            logger.info(f"3x-ui: ✅ срок для {email} обновлён до {expiry_days} дней")
            return True
        
        logger.error(f"3x-ui: ❌ ошибка обновления срока: {result}")
        return False

    async def get_client_link(self, email: str) -> str | None:
        """Генерирует ссылку для подключения клиента."""
        try:
            if config.SUB_LINKS and len(config.SUB_LINKS) > 0:
                template = config.SUB_LINKS[0]
                if "{email}" in template:
                    return template.format(email=email)
                base = "/".join(template.split("/")[:-1])
                return f"{base}/{email}"
            
            parsed = urlparse(config.XUI_HOST)
            domain = parsed.netloc.split(":")[0]
            return f"https://{domain}:2096/sub/{email}"
        except Exception as e:
            logger.error(f"3x-ui: ошибка генерации ссылки - {e}")
            return None

    async def remove_client(self, client_id: str) -> bool:
        """Удаляет клиента по email."""
        logger.info(f"3x-ui: удаление клиента {client_id}")
        
        is_uuid = bool(re.match(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            client_id,
            re.IGNORECASE
        ))
        
        if is_uuid:
            inbounds = await self._get_active_inbounds()
            for inbound in inbounds:
                settings = inbound.get("settings", {})
                if isinstance(settings, str):
                    settings = json.loads(settings)
                for client in settings.get("clients", []):
                    if client.get("id") == client_id:
                        email = client.get("email")
                        if email:
                            return await self.remove_client(email)
            logger.error(f"3x-ui: ❌ клиент с UUID {client_id} не найден")
            return False
        
        email = client_id
        result = await self._api_post(f"/panel/api/clients/del/{email}")
        if result and result.get("success"):
            logger.info(f"3x-ui: ✅ клиент {email} удалён")
            return True
        
        logger.error(f"3x-ui: ❌ ошибка удаления клиента: {result}")
        return False

    async def client_exists(self, email: str) -> bool:
        """Проверяет, существует ли клиент с указанным email."""
        client = await self._get_client_by_email(email)
        return client is not None

    async def close(self):
        if self._session:
            await self._session.aclose()
            self._session = None


xray = XRayAPI()