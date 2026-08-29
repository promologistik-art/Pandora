import httpx
import logging
import re
import json
import asyncio
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
        """
        Получить список активных inbound'ов.
        Исключаем inbound'ы 8 и 9 (они выключены).
        """
        inbounds = await self._get_inbounds()
        # Фильтруем: только включённые (enable=True)
        active = [inb for inb in inbounds if inb.get("enable", False)]
        logger.info(f"3x-ui: активные inbound'ы: {[inb.get('id') for inb in active]}")
        return active

    async def _get_client_by_email(self, email: str) -> dict | None:
        """Получить клиента по email через /clients/get/{email}."""
        result = await self._api_get(f"/panel/api/clients/get/{email}")
        if result and result.get("success"):
            return result.get("obj")
        return None

    async def _get_client_uuid_by_email(self, email: str, retries: int = 5) -> str | None:
        """
        Получить UUID (id) клиента по email с повторными попытками.
        Панель генерирует UUID после создания, поэтому нужны повторные попытки.
        """
        for attempt in range(retries):
            if attempt > 0:
                wait_time = 1 * attempt
                logger.info(f"3x-ui: ждём {wait_time} сек перед повторной попыткой получения UUID для {email}")
                await asyncio.sleep(wait_time)
            
            client = await self._get_client_by_email(email)
            if client:
                client_uuid = client.get("id")
                if client_uuid:
                    logger.info(f"3x-ui: ✅ получен UUID {client_uuid} для {email}")
                    return client_uuid
            
            logger.warning(f"3x-ui: попытка {attempt + 1}/{retries} - UUID для {email} не получен")
        
        return None

    async def add_client(self, email: str, expiry_days: int = 30) -> dict | None:
        """
        Создаёт клиента через /clients/add с указанием inboundIds.
        Использует ТОЧНО ТАКОЙ ЖЕ формат, как панель при создании через интерфейс.
        
        Поля, которые генерирует панель (НЕ ПЕРЕДАЁМ):
        - id (UUID)
        - password
        - auth (Hysteria Auth)
        
        Поля, которые ОБЯЗАТЕЛЬНО передаём:
        - email (ID подписки)
        - subId = email (тоже ID подписки)
        - inboundIds (массив активных inbound'ов)
        - expiryTime (расчётное значение)
        """
        logger.info(f"3x-ui: создание клиента {email}, срок {expiry_days} дней")
        
        # 1. Получаем все активные inbound'ы
        active_inbounds = await self._get_active_inbounds()
        if not active_inbounds:
            logger.error("3x-ui: нет активных inbound'ов")
            return None
        
        inbound_ids = [inb.get("id") for inb in active_inbounds]
        logger.info(f"3x-ui: привязываем клиента к inbound'ам: {inbound_ids}")
        
        # 2. Вычисляем expiryTime (в миллисекундах)
        expiry_time = int((datetime.utcnow() + timedelta(days=expiry_days)).timestamp() * 1000)
        logger.info(f"3x-ui: expiryTime = {expiry_time} ({expiry_days} дней)")
        
        # 3. Формируем payload ТОЧНО как в панели
        client_payload = {
            "email": email,
            "subId": email,                    # ✅ subId = email (ID подписки)
            "enable": True,
            "expiryTime": expiry_time,
            "limitIp": 3,
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
            # ❌ НЕ передаём: id, password, auth (генерируются панелью)
        }
        
        payload = {
            "client": client_payload,
            "inboundIds": inbound_ids,
        }
        
        result = await self._api_post("/panel/api/clients/add", payload)
        
        if result and result.get("success"):
            logger.info(f"3x-ui: ✅ клиент {email} создан, ожидаем генерацию UUID...")
            
            # Ждём, пока панель сгенерирует UUID
            real_uuid = await self._get_client_uuid_by_email(email, retries=5)
            
            if real_uuid:
                logger.info(f"3x-ui: ✅ клиент {email} создан с UUID {real_uuid}")
                return {
                    "uuid": real_uuid,
                    "email": email,
                }
            else:
                # Если UUID не получен - пробуем получить данные клиента
                client_data = await self._get_client_by_email(email)
                if client_data:
                    logger.info(f"3x-ui: ✅ клиент {email} создан, данные получены")
                    return {
                        "uuid": client_data.get("id"),
                        "email": email,
                    }
                else:
                    logger.warning(f"3x-ui: клиент {email} создан, но данные не получены")
                    return {
                        "uuid": None,
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
        
        # 2. Обновляем клиента (сохраняя все остальные поля)
        payload = {
            "email": email,
            "subId": client.get("subId", email),
            "enable": client.get("enable", True),
            "expiryTime": expiry_time,
            "limitIp": client.get("limitIp", 3),
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
        """
        Генерирует ссылку для подключения клиента.
        """
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
        """
        Удаляет клиента по email.
        """
        logger.info(f"3x-ui: удаление клиента {client_id}")
        
        # Проверяем, является ли client_id UUID
        is_uuid = bool(re.match(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            client_id,
            re.IGNORECASE
        ))
        
        if is_uuid:
            # Если это UUID, пробуем найти email
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
        
        # Удаляем по email
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