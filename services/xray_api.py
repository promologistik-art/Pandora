import httpx
import logging
from config import config

logger = logging.getLogger(__name__)


class XRayAPI:
    def __init__(self):
        self.base_url = config.XUI_HOST.rstrip("/")
        self.api_token = getattr(config, 'XUI_API_TOKEN', None)
        self._session: httpx.AsyncClient | None = None

    async def _get_session(self) -> httpx.AsyncClient:
        if self._session is None:
            self._session = httpx.AsyncClient(verify=False, timeout=30.0)
        return self._session

    async def _api_get(self, path: str) -> dict | None:
        session = await self._get_session()
        headers = {"Authorization": f"Bearer {self.api_token}"} if self.api_token else {}
        try:
            url = f"{self.base_url}{path}"
            logger.info(f"3x-ui GET: {url} | Token: {self.api_token[:8]}...")
            resp = await session.get(url, headers=headers)
            logger.info(f"3x-ui Response: {resp.status_code}")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"3x-ui: ошибка GET {path} - {e}")
            return None

    async def _api_post(self, path: str, json_data: dict) -> dict | None:
        session = await self._get_session()
        headers = {"Authorization": f"Bearer {self.api_token}"} if self.api_token else {}
        try:
            url = f"{self.base_url}{path}"
            logger.info(f"3x-ui POST: {url}")
            resp = await session.post(url, json=json_data, headers=headers)
            logger.info(f"3x-ui Response: {resp.status_code}")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"3x-ui: ошибка POST {path} - {e}")
            return None

    async def _get_inbound(self) -> dict | None:
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
        inbound = await self._get_inbound()
        if not inbound:
            return None

        settings = inbound.get("settings", {})
        if isinstance(settings, str):
            import json
            settings = json.loads(settings)

        clients = settings.get("clients", [])
        clients.append({
            "email": email,
            "id": uuid,
            "enable": True,
            "flow": "xtls-rprx-vision"
        })
        settings["clients"] = clients

        result = await self._api_post(
            f"/panel/api/inbounds/update/{config.XUI_INBOUND_ID}",
            {"id": config.XUI_INBOUND_ID, "settings": settings}
        )

        if result and result.get("success"):
            logger.info(f"3x-ui: клиент {email} добавлен")
            return {
                "uuid": uuid,
                "email": email,
                "host": "dashoguz.mooo.com",
            }
        logger.error(f"3x-ui: ошибка добавления клиента - {result}")
        return None

    async def remove_client(self, uuid: str) -> bool:
        inbound = await self._get_inbound()
        if not inbound:
            return False

        settings = inbound.get("settings", {})
        if isinstance(settings, str):
            import json
            settings = json.loads(settings)

        clients = settings.get("clients", [])
        new_clients = [c for c in clients if c.get("id") != uuid]
        if len(new_clients) == len(clients):
            return False

        settings["clients"] = new_clients
        result = await self._api_post(
            f"/panel/api/inbounds/update/{config.XUI_INBOUND_ID}",
            {"id": config.XUI_INBOUND_ID, "settings": settings}
        )
        return result and result.get("success", False)

    async def close(self):
        if self._session:
            await self._session.aclose()
            self._session = None


xray = XRayAPI()