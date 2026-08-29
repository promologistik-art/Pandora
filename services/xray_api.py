import httpx
import logging
import secrets
import json
import uuid
import asyncio
import re
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
            logger.info(f"3x-ui Payload: {json.dumps(json_data or {}, indent=2)}")
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
        return [inb for inb in inbounds if inb.get("enable", False)]

    async def _get_client_by_email(self, email: str) -> dict | None:
        """Получить клиента по email через /clients/get/{email}."""
        result = await self._api_get(f"/panel/api/clients/get/{email}")
        if result and result.get("success"):
            return result.get("obj")
        return None

    async def _get_client_uuid_by_email(self, email: str) -> str | None:
        """Получить UUID клиента по email."""
        client = await self._get_client_by_email(email)
        if client:
            return client.get("id")
        return None

    async def add_client(self, email: str, expiry_days: int = 30) -> dict | None:
        """
        Создаёт клиента и привязывает ко всем активным inbound'ам через /clients/add.
        """
        logger.info(f"3x-ui: создание клиента {email}, срок {expiry_days} дней")
        
        # 1. Получаем все активные inbound'ы
        active_inbounds = await self._get_active_inbounds()
        if not active_inbounds:
            logger.error("3x-ui: нет активных inbound'ов")
            return None
        
        inbound_ids = [inb.get("id") for inb in active_inbounds]
        logger.info(f"3x-ui: привязываем клиента к inbound'ам: {inbound_ids}")
        
        # 2. Создаём клиента через /clients/add
        expiry_time = int((datetime.utcnow() + timedelta(days=expiry_days)).timestamp() * 1000)
        
        # ✅ ИСПРАВЛЕНО: Добавлено обязательное поле email
        payload = {
            "email": email,                              # ✅ ОБЯЗАТЕЛЬНОЕ поле
            "enable": True,
            "expiryTime": expiry_time,
            "limitIp": 3,
            "totalGB": 0,
            "inboundIds": inbound_ids,
        }
        
        result = await self._api_post("/panel/api/clients/add", payload)
        
        if result and result.get("success"):
            client_data = result.get("obj", {})
            client_uuid = client_data.get("id")
            
            if not client_uuid:
                client_uuid = await self._get_client_uuid_by_email(email)
            
            logger.info(f"3x-ui: ✅ клиент {email} создан и привязан к {len(inbound_ids)} inbound'ам")
            return {
                "uuid": client_uuid,
                "email": email,
            }
        
        logger.error(f"3x-ui: ❌ ошибка создания клиента: {result}")
        return None

    async def update_client_expiry(self, email: str, expiry_days: int) -> bool:
        """
        Обновляет срок клиента через /clients/update/{email}.
        """
        logger.info(f"3x-ui: обновление срока для {email} до {expiry_days} дней")
        
        # 1. Получаем текущего клиента
        client = await self._get_client_by_email(email)
        if not client:
            logger.error(f"3x-ui: клиент {email} не найден")
            return False
        
        expiry_time = int((datetime.utcnow() + timedelta(days=expiry_days)).timestamp() * 1000)
        
        # 2. Обновляем только срок
        payload = {
            "email": email,
            "enable": client.get("enable", True),
            "expiryTime": expiry_time,
            "limitIp": client.get("limitIp", 3),
            "totalGB": client.get("totalGB", 0),
            "inboundIds": client.get("inboundIds", []),
        }
        
        result = await self._api_post(f"/panel/api/clients/update/{email}", payload)
        
        if result and result.get("success"):
            logger.info(f"3x-ui: ✅ срок для {email} обновлён до {expiry_days} дней")
            return True
        
        logger.error(f"3x-ui: ❌ ошибка обновления срока: {result}")
        return False

    async def get_client_link(self, email: str) -> str | None:
        """
        Генерирует ссылку для подключения клиента.
        Использует SUB_LINKS из конфига как шаблон.
        """
        try:
            if config.SUB_LINKS and len(config.SUB_LINKS) > 0:
                template = config.SUB_LINKS[0]
                # Если в шаблоне есть {email}, заменяем
                if "{email}" in template:
                    return template.format(email=email)
                # Иначе используем как базовый URL
                base = "/".join(template.split("/")[:-1])
                return f"{base}/{email}"
            
            # Если SUB_LINKS нет, используем домен из XUI_HOST
            parsed = urlparse(config.XUI_HOST)
            domain = parsed.netloc.split(":")[0]
            return f"https://{domain}:2096/sub/{email}"
        except Exception as e:
            logger.error(f"3x-ui: ошибка генерации ссылки - {e}")
            return None

    async def remove_client(self, client_id: str) -> bool:
        """
        Удаляет клиента по email или UUID.
        Поддерживает оба формата для обратной совместимости.
        """
        logger.info(f"3x-ui: удаление клиента {client_id}")
        
        # Проверяем, является ли client_id UUID
        is_uuid = bool(re.match(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            client_id,
            re.IGNORECASE
        ))
        
        if is_uuid:
            # Если это UUID, пробуем найти клиента по email через поиск
            # Вариант 1: пробуем удалить по UUID через API (если поддерживается)
            result = await self._api_post(f"/panel/api/clients/del/{client_id}")
            if result and result.get("success"):
                logger.info(f"3x-ui: ✅ клиент {client_id} удалён по UUID")
                return True
            
            # Вариант 2: пробуем найти email по UUID через список клиентов
            logger.warning(f"3x-ui: не удалось удалить по UUID {client_id}, пробуем найти email")
            inbounds = await self._get_active_inbounds()
            for inbound in inbounds:
                for client in inbound.get("settings", {}).get("clients", []):
                    if client.get("id") == client_id:
                        email = client.get("email")
                        if email:
                            logger.info(f"3x-ui: найден email {email} для UUID {client_id}")
                            return await self.remove_client(email)
            
            logger.error(f"3x-ui: ❌ клиент с UUID {client_id} не найден")
            return False
        else:
            # Это email - удаляем напрямую
            result = await self._api_post(f"/panel/api/clients/del/{client_id}")
            if result and result.get("success"):
                logger.info(f"3x-ui: ✅ клиент {client_id} удалён")
                return True
            
            logger.error(f"3x-ui: ❌ ошибка удаления клиента: {result}")
            return False

    async def client_exists(self, email: str) -> bool:
        """Проверяет, существует ли клиент с указанным email."""
        client = await self._get_client_by_email(email)
        return client is not None

    async def get_client_traffic(self, email: str) -> dict | None:
        """Получает трафик клиента по email."""
        client = await self._get_client_by_email(email)
        if client:
            return {
                "up": client.get("up", 0),
                "down": client.get("down", 0),
                "total": client.get("total", 0),
            }
        return None

    async def close(self):
        if self._session:
            await self._session.aclose()
            self._session = None


xray = XRayAPI()