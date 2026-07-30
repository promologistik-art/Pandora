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
            
            if resp.status_code == 403:
                logger.error("3x-ui: 403 Forbidden — неверный токен")
                return None
            if resp.status_code == 401:
                logger.error("3x-ui: 401 Unauthorized — токен недействителен")
                return None
            if resp.status_code == 404:
                logger.error("3x-ui: 404 Not Found — проверьте путь")
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

            if resp.status_code == 403:
                logger.error("3x-ui: 403 Forbidden — неверный токен")
                return None
            if resp.status_code == 401:
                logger.error("3x-ui: 401 Unauthorized — токен недействителен")
                return None
            if resp.status_code == 404:
                logger.error("3x-ui: 404 Not Found — проверьте путь")
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

    async def _get_inbound(self) -> dict | None:
        data = await self._api_get("/panel/api/inbounds/list")
        if not data or not data.get("success"):
            return None
        for inbound in data.get("obj", []):
            if inbound.get("id") == config.XUI_INBOUND_ID:
                return inbound
        return None

    async def _get_client_uuid_by_email(self, email: str) -> str | None:
        data = await self._api_get("/panel/api/inbounds/list")
        if not data or not data.get("success"):
            return None
        
        for inbound in data.get("obj", []):
            settings = inbound.get("settings", {})
            if isinstance(settings, str):
                settings = json.loads(settings)
            for client in settings.get("clients", []):
                if client.get("email") == email:
                    return client.get("id")
            for client in inbound.get("clientStats", []):
                if client.get("email") == email:
                    return client.get("uuid")
        return None

    async def add_client(self, email: str, uuid: str = None, expiry_days: int = 30) -> dict | None:
        logger.info(f"3x-ui: создание клиента {email}, срок {expiry_days} дней")
        inbound = await self._get_inbound()
        if not inbound:
            logger.error("3x-ui: inbound не найден")
            return None

        settings = inbound.get("settings", {})
        if isinstance(settings, str):
            settings = json.loads(settings)

        auth = secrets.token_hex(8)
        expiry_time = int((datetime.utcnow() + timedelta(days=expiry_days)).timestamp() * 1000)

        clients = settings.get("clients", [])
        client_data = {
            "email": email,
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
        }
        if uuid:
            client_data["id"] = uuid

        clients.append(client_data)
        settings["clients"] = clients

        # ✅ ВСЕ ПОЛЯ, ВКЛЮЧАЯ originNodeGuid
        update_data = {
            "id": inbound.get("id"),
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
            f"/panel/api/inbounds/update/{config.XUI_INBOUND_ID}",
            update_data
        )

        if result and result.get("success"):
            logger.info(f"3x-ui: клиент {email} добавлен")
            import asyncio
            for _ in range(5):
                await asyncio.sleep(1)
                real_uuid = await self._get_client_uuid_by_email(email)
                if real_uuid:
                    logger.info(f"3x-ui: получен UUID {real_uuid}")
                    return {"uuid": real_uuid, "email": email}
            logger.warning(f"3x-ui: не удалось получить UUID для {email}")
            return None
        
        logger.error(f"3x-ui: ошибка добавления клиента")
        return None

    async def update_client_expiry(self, email: str, expiry_days: int) -> bool:
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
            "flow": "xtls-rprx-vision",
        }
        result = await self._api_post(
            f"/panel/api/inbounds/updateClient/{config.XUI_INBOUND_ID}/{client_uuid}",
            update_data
        )
        return result and result.get("success")

    async def get_client_link(self, email: str) -> str | None:
        try:
            if config.SUB_LINKS and len(config.SUB_LINKS) > 0:
                template = config.SUB_LINKS[0]
                base = "/".join(template.split("/")[:-1])
                return f"{base}/{email}"
            return f"https://dashoguz.mooo.com:2096/sub/{email}"
        except Exception as e:
            logger.error(f"3x-ui: ошибка генерации ссылки - {e}")
            return None

    async def remove_client(self, uuid: str) -> bool:
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
            "id": inbound.get("id"),
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
            f"/panel/api/inbounds/update/{config.XUI_INBOUND_ID}",
            update_data
        )
        return result and result.get("success", False)

    async def close(self):
        if self._session:
            await self._session.aclose()
            self._session = None


xray = XRayAPI()