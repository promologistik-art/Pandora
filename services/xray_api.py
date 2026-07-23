import httpx
import logging
import secrets
import json
from config import config

logger = logging.getLogger(__name__)


class XRayAPI:
    def __init__(self):
        self.base_url = config.XUI_HOST.rstrip("/")
        self.username = config.XUI_USERNAME
        self.password = config.XUI_PASSWORD
        self._session: httpx.AsyncClient | None = None
        self._cookies: dict | None = None

    async def _get_session(self) -> httpx.AsyncClient:
        if self._session is None:
            self._session = httpx.AsyncClient(verify=False, timeout=30.0)
        return self._session

    async def login(self) -> bool:
        """Авторизация в 3x-ui через Cookie."""
        try:
            session = await self._get_session()
            login_data = {
                "username": self.username,
                "password": self.password
            }
            url = f"{self.base_url}/login"
            logger.info(f"3x-ui LOGIN: {url}")

            resp = await session.post(url, json=login_data)
            logger.info(f"3x-ui Login Response: {resp.status_code}")

            if resp.status_code == 200:
                # Сохраняем cookies для дальнейших запросов
                self._cookies = dict(resp.cookies)
                logger.info("3x-ui: авторизация успешна")
                return True
            else:
                logger.error(f"3x-ui: ошибка авторизации - {resp.text}")
                return False
        except Exception as e:
            logger.error(f"3x-ui: ошибка при авторизации - {e}")
            return False

    async def _ensure_auth(self) -> bool:
        """Проверяет авторизацию и перелогинивается при необходимости."""
        if not self._cookies:
            return await self.login()
        return True

    async def _api_get(self, path: str) -> dict | None:
        """Приватный метод GET запроса."""
        if not await self._ensure_auth():
            return None

        session = await self._get_session()
        try:
            url = f"{self.base_url}{path}"
            logger.info(f"3x-ui GET: {url}")
            resp = await session.get(url, cookies=self._cookies)
            logger.info(f"3x-ui Response: {resp.status_code}")

            if resp.status_code == 401:
                # Сессия истекла, пробуем перелогиниться
                logger.warning("3x-ui: сессия истекла, пробуем перелогиниться")
                if await self.login():
                    resp = await session.get(url, cookies=self._cookies)

            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"3x-ui: ошибка GET {path} - {e}")
            return None

    async def _api_post(self, path: str, json_data: dict) -> dict | None:
        """Приватный метод POST запроса."""
        if not await self._ensure_auth():
            return None

        session = await self._get_session()
        try:
            url = f"{self.base_url}{path}"
            logger.info(f"3x-ui POST: {url}")
            resp = await session.post(url, json=json_data, cookies=self._cookies)
            logger.info(f"3x-ui Response: {resp.status_code}")

            if resp.status_code == 401:
                logger.warning("3x-ui: сессия истекла, пробуем перелогиниться")
                if await self.login():
                    resp = await session.post(url, json=json_data, cookies=self._cookies)

            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"3x-ui: ошибка POST {path} - {e}")
            return None

    async def check_health(self) -> bool:
        """Публичный метод проверки доступности 3x-ui."""
        data = await self._api_get("/panel/api/inbounds/list")
        return data and data.get("success", False)

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

    async def add_client(self, email: str, uuid: str) -> dict | None:
        """Добавить клиента в 3x-ui."""
        inbound = await self._get_inbound()
        if not inbound:
            return None

        settings = inbound.get("settings", {})
        if isinstance(settings, str):
            settings = json.loads(settings)

        auth = secrets.token_hex(8)

        clients = settings.get("clients", [])
        clients.append({
            "email": email,
            "id": uuid,
            "enable": True,
            "flow": "xtls-rprx-vision",
            "auth": auth,
            "password": auth,
            "subId": email,
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": 0,
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
            logger.info(f"3x-ui: клиент {email} добавлен")
            return {
                "uuid": uuid,
                "email": email,
                "host": self.base_url.split("//")[1].split(":")[0] if "//" in self.base_url else "dashoguz.mooo.com",
                "auth": auth,
            }
        logger.error(f"3x-ui: ошибка добавления клиента - {result}")
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