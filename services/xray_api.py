import httpx
import logging
from config import config

logger = logging.getLogger(__name__)


class XRayAPI:
    """Обёртка над API 3x-ui. Поддерживает cookie-авторизацию и Bearer-токен."""

    def __init__(self):
        self.base_url = config.XUI_HOST.rstrip("/")
        self._session: httpx.AsyncClient | None = None
        self._cookie: str | None = None

    async def _get_session(self) -> httpx.AsyncClient:
        if self._session is None:
            self._session = httpx.AsyncClient(verify=False, timeout=30.0)
        return self._session

    async def login(self) -> bool:
        """Вход в панель. Пробует Bearer-токен, затем cookie."""
        session = await self._get_session()

        # Способ 1: API-токен (если есть в конфиге)
        api_token = getattr(config, 'XUI_API_TOKEN', None)
        if api_token:
            session.headers["Authorization"] = f"Bearer {api_token}"
            # Проверяем токен
            try:
                resp = await session.get(f"{self.base_url}/panel/api/inbounds/list")
                if resp.status_code == 200:
                    logger.info("3x-ui: авторизация через API-токен успешна")
                    return True
            except Exception:
                pass
            # Токен не сработал — сбрасываем и пробуем cookie
            session.headers.pop("Authorization", None)

        # Способ 2: Логин/пароль
        try:
            resp = await session.post(
                f"{self.base_url}/login",
                json={
                    "username": config.XUI_USERNAME,
                    "password": config.XUI_PASSWORD
                }
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                self._cookie = resp.cookies.get("3x-ui") or resp.cookies.get("session")
                if self._cookie:
                    session.cookies.set("3x-ui", self._cookie, domain=config.XUI_HOST.split("://")[1].split(":")[0].split("/")[0])
                logger.info("3x-ui: логин через cookie успешен")
                return True
            logger.error(f"3x-ui: ошибка логина - {data}")
            return False
        except Exception as e:
            logger.error(f"3x-ui: ошибка подключения - {e}")
            return False

    async def _ensure_auth(self):
        if not self._cookie and "Authorization" not in (await self._get_session()).headers:
            await self.login()

    async def _api_get(self, path: str) -> dict | None:
        await self._ensure_auth()
        session = await self._get_session()
        try:
            url = f"{self.base_url}{path}"
            resp = await session.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"3x-ui: ошибка GET {path} - {e}")
            return None

    async def _api_post(self, path: str, json_data: dict) -> dict | None:
        await self._ensure_auth()
        session = await self._get_session()
        try:
            url = f"{self.base_url}{path}"
            resp = await session.post(url, json=json_data)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"3x-ui: ошибка POST {path} - {e}")
            return None

    async def add_client(self, email: str, uuid: str) -> dict | None:
        """Добавляет клиента в inbound через clients API."""
        # Способ 1: через /panel/api/clients/add (предпочтительный)
        result = await self._api_post(
            "/panel/api/clients/add",
            {
                "client": {
                    "email": email,
                    "id": uuid,
                    "enable": True,
                    "flow": "xtls-rprx-vision",
                    "totalGB": 0,
                    "expiryTime": 0,
                    "limitIp": 0,
                    "tgId": 0,
                    "subId": email,
                },
                "inboundIds": [config.XUI_INBOUND_ID]
            }
        )
        if result and result.get("success"):
            logger.info(f"3x-ui: клиент {email} добавлен")
            host = config.XUI_HOST.split("://")[1].split(":")[0].split("/")[0] if "://" in config.XUI_HOST else config.XUI_HOST
            return {"uuid": uuid, "email": email, "host": host}

        # Способ 2: через inbound update (fallback)
        logger.warning("3x-ui: /clients/add не сработал, пробую inbound update")
        return await self._add_client_via_inbound(email, uuid)

    async def _add_client_via_inbound(self, email: str, uuid: str) -> dict | None:
        """Добавляет клиента напрямую в inbound settings."""
        data = await self._api_get(f"/panel/api/inbounds/get/{config.XUI_INBOUND_ID}")
        if not data or not data.get("success"):
            return None

        inbound_data = data.get("obj", {})
        settings = inbound_data.get("settings", {})
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
            logger.info(f"3x-ui: клиент {email} добавлен через inbound update")
            host = config.XUI_HOST.split("://")[1].split(":")[0].split("/")[0] if "://" in config.XUI_HOST else config.XUI_HOST
            return {"uuid": uuid, "email": email, "host": host}
        return None

    async def remove_client(self, email: str) -> bool:
        """Удаляет клиента по email."""
        result = await self._api_post(
            f"/panel/api/clients/del/{email}",
            {}
        )
        return result and result.get("success", False)

    async def get_client_traffic(self, email: str) -> dict | None:
        """Получает статистику трафика клиента."""
        data = await self._api_get(f"/panel/api/clients/traffic/{email}")
        if data and data.get("success"):
            obj = data.get("obj", {})
            return {
                "up": obj.get("up", 0),
                "down": obj.get("down", 0),
                "total": obj.get("total", 0),
            }
        return None

    async def close(self):
        if self._session:
            await self._session.aclose()
            self._session = None
            self._cookie = None


xray = XRayAPI()