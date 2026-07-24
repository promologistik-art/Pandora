import httpx
import logging
import secrets
import json
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
        """GET запрос с Bearer-токеном."""
        if not self.api_token:
            logger.error("3x-ui: API-токен не настроен! Добавьте XUI_API_TOKEN в .env")
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
            
            if resp.status_code == 403:
                logger.error("3x-ui: 403 Forbidden — неверный API-токен или недостаточно прав")
                return None
            if resp.status_code == 401:
                logger.error("3x-ui: 401 Unauthorized — токен недействителен")
                return None

            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"3x-ui: HTTP ошибка {path} - {e.response.status_code}")
            logger.error(f"3x-ui: response body: {e.response.text[:500]}")
            return None
        except Exception as e:
            logger.error(f"3x-ui: ошибка GET {path} - {e}")
            return None

    async def _api_post(self, path: str, json_data: dict) -> dict | None:
        """POST запрос с Bearer-токеном."""
        if not self.api_token:
            logger.error("3x-ui: API-токен не настроен! Добавьте XUI_API_TOKEN в .env")
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
            resp = await session.post(url, json=json_data, headers=headers)
            logger.info(f"3x-ui Response: {resp.status_code}")

            if resp.status_code == 403:
                logger.error("3x-ui: 403 Forbidden — неверный API-токен или недостаточно прав")
                return None
            if resp.status_code == 401:
                logger.error("3x-ui: 401 Unauthorized — токен недействителен")
                return None

            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"3x-ui: HTTP ошибка {path} - {e.response.status_code}")
            logger.error(f"3x-ui: response body: {e.response.text[:500]}")
            return None
        except Exception as e:
            logger.error(f"3x-ui: ошибка POST {path} - {e}")
            return None

    async def check_health(self) -> bool:
        """Проверка доступности 3x-ui."""
        try:
            data = await self._api_get("/panel/api/inbounds/list")
            if data and data.get("success"):
                logger.info("3x-ui health check: OK")
                return True
            else:
                logger.warning(f"3x-ui health check: failed - {data}")
                return False
        except Exception as e:
            logger.error(f"3x-ui health check: error - {e}")
            return False

    async def _get_inbound(self) -> dict | None:
        """Получить информацию о inbound."""
        data = await self._api_get("/panel/api/inbounds/list")
        if not data or not data.get("success"):
            logger.error("3x-ui: не удалось получить список inbound")
            return None

        inbounds = data.get("obj", [])
        for inbound in inbounds:
            if inbound.get("id") == config.XUI_INBOUND_ID:
                return inbound

        logger.error(f"3x-ui: inbound с ID {config.XUI_INBOUND_ID} не найден")
        return None

    async def add_client(self, email: str, uuid: str, expiry_days: int = 30) -> dict | None:
        """Добавить клиента в 3x-ui с указанием срока действия."""
        logger.info(f"3x-ui: НАЧАЛО создания клиента {email}, срок {expiry_days} дней")
        inbound = await self._get_inbound()
        if not inbound:
            logger.error("3x-ui: inbound не найден, создание клиента невозможно")
            return None

        settings = inbound.get("settings", {})
        if isinstance(settings, str):
            settings = json.loads(settings)

        auth = secrets.token_hex(8)
        
        # Считаем expiryTime в миллисекундах
        if expiry_days > 0:
            expiry_time = int((datetime.utcnow() + timedelta(days=expiry_days)).timestamp() * 1000)
        else:
            expiry_time = 0

        clients = settings.get("clients", [])
        clients.append({
            "email": email,
            "id": uuid,
            "enable": True,
            "flow": "xtls-rprx-vision",
            "auth": auth,
            "password": auth,
            "subId": email,
            "limitIp": 3,
            "totalGB": 0,
            "expiryTime": expiry_time,
            "tgId": 0,
            "security": "auto",
            "reset": 0,
        })
        settings["clients"] = clients

        update_data = {
            "id": config.XUI_INBOUND_ID,
            "protocol": inbound.get("protocol", "vless"),
            "port": inbound.get("port", 47725),
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
        }

        result = await self._api_post(
            f"/panel/api/inbounds/update/{config.XUI_INBOUND_ID}",
            update_data
        )

        if result and result.get("success"):
            logger.info(f"3x-ui: клиент {email} добавлен, действует до {expiry_days} дней")
            return {
                "uuid": uuid,
                "email": email,
                "auth": auth,
            }
        logger.error(f"3x-ui: ошибка добавления клиента - {result}")
        return None

    async def get_client_link(self, email: str) -> str | None:
        """Получить ссылку на клиента из 3x-ui."""
        try:
            data = await self._api_get(f"/panel/api/inbounds/get/{config.XUI_INBOUND_ID}/client/{email}/link")
            if data and data.get("success"):
                return data.get("obj")
            else:
                logger.warning(f"3x-ui: не удалось получить ссылку для {email}")
                return None
        except Exception as e:
            logger.error(f"3x-ui: ошибка получения ссылки для {email} - {e}")
            return None

    async def remove_client(self, uuid: str) -> bool:
        """Удалить клиента из 3x-ui."""
        inbound = await self._get_inbound()
        if not inbound:
            return False

        settings = inbound.get("settings", {})
        if isinstance(settings, str):
            settings = json.loads(settings)

        clients = settings.get("clients", [])
        new_clients = [c for c in clients if c.get("id") != uuid]
        if len(new_clients) == len(clients):
            return False

        settings["clients"] = new_clients

        update_data = {
            "id": config.XUI_INBOUND_ID,
            "protocol": inbound.get("protocol", "vless"),
            "port": inbound.get("port", 47725),
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
        }

        result = await self._api_post(
            f"/panel/api/inbounds/update/{config.XUI_INBOUND_ID}",
            update_data
        )
        return result and result.get("success", False)

    async def close(self):
        if self._session:
            await self._session.aclose()
            self._session = None


xray = XRayAPI()