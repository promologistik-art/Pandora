import httpx
import logging
from config import config

logger = logging.getLogger(__name__)


class XRayAPI:
    """Обёртка над API 3x-ui."""

    def __init__(self):
        self.base_url = config.XUI_HOST.rstrip("/")
        self.auth = (config.XUI_USERNAME, config.XUI_PASSWORD)
        self._session: httpx.AsyncClient | None = None
        self._cookie: str | None = None

    async def _get_session(self) -> httpx.AsyncClient:
        if self._session is None:
            self._session = httpx.AsyncClient(verify=False, timeout=30.0)
        return self._session

    async def login(self) -> bool:
        """Вход в панель, сохраняет куки сессии."""
        session = await self._get_session()
        try:
            resp = await session.post(
                f"{self.base_url}/login",
                data={"username": self.auth[0], "password": self.auth[1]}
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                self._cookie = resp.cookies.get("session")
                session.cookies.set("session", self._cookie or "")
                logger.info("3x-ui: логин успешен")
                return True
            logger.error(f"3x-ui: ошибка логина - {data}")
            return False
        except Exception as e:
            logger.error(f"3x-ui: ошибка подключения - {e}")
            return False

    async def _ensure_auth(self):
        """Проверяет авторизацию, при необходимости логинится."""
        if not self._cookie:
            await self.login()

    async def add_client(self, email: str, uuid: str) -> dict | None:
        """Добавляет клиента в inbound. Возвращает ответ API."""
        await self._ensure_auth()
        session = await self._get_session()
        try:
            # Получаем текущие настройки inbound
            resp = await session.get(
                f"{self.base_url}/panel/api/inbounds/get/{config.XUI_INBOUND_ID}"
            )
            resp.raise_for_status()
            inbound_data = resp.json().get("obj", {})

            # Извлекаем настройки клиентов (может быть в settings или streamSettings)
            settings = inbound_data.get("settings", {})
            if isinstance(settings, str):
                import json
                settings = json.loads(settings)

            clients = settings.get("clients", [])

            # Добавляем нового клиента
            new_client = {
                "email": email,
                "id": uuid,
                "enable": True,
                "flow": "xtls-rprx-vision"
            }
            clients.append(new_client)
            settings["clients"] = clients

            # Обновляем inbound
            update_data = {
                "id": config.XUI_INBOUND_ID,
                "settings": settings
            }
            resp = await session.post(
                f"{self.base_url}/panel/api/inbounds/update/{config.XUI_INBOUND_ID}",
                json=update_data
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("success"):
                logger.info(f"3x-ui: клиент {email} добавлен")
                # Возвращаем ключ для подключения
                return {
                    "uuid": uuid,
                    "email": email,
                    "host": config.XUI_HOST.split("://")[1].split(":")[0] if "://" in config.XUI_HOST else config.XUI_HOST,
                }
            logger.error(f"3x-ui: ошибка добавления клиента - {result}")
            return None
        except Exception as e:
            logger.error(f"3x-ui: исключение при добавлении клиента - {e}")
            return None

    async def remove_client(self, uuid: str) -> bool:
        """Удаляет клиента из inbound по UUID."""
        await self._ensure_auth()
        session = await self._get_session()
        try:
            resp = await session.get(
                f"{self.base_url}/panel/api/inbounds/get/{config.XUI_INBOUND_ID}"
            )
            resp.raise_for_status()
            inbound_data = resp.json().get("obj", {})

            settings = inbound_data.get("settings", {})
            if isinstance(settings, str):
                import json
                settings = json.loads(settings)

            clients = settings.get("clients", [])
            new_clients = [c for c in clients if c.get("id") != uuid]

            if len(new_clients) == len(clients):
                logger.warning(f"3x-ui: клиент {uuid} не найден для удаления")
                return False

            settings["clients"] = new_clients
            update_data = {
                "id": config.XUI_INBOUND_ID,
                "settings": settings
            }
            resp = await session.post(
                f"{self.base_url}/panel/api/inbounds/update/{config.XUI_INBOUND_ID}",
                json=update_data
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("success"):
                logger.info(f"3x-ui: клиент {uuid} удалён")
                return True
            return False
        except Exception as e:
            logger.error(f"3x-ui: исключение при удалении клиента - {e}")
            return False

    async def get_client_traffic(self, uuid: str) -> dict | None:
        """Получает статистику трафика клиента."""
        await self._ensure_auth()
        session = await self._get_session()
        try:
            resp = await session.get(
                f"{self.base_url}/panel/api/inbounds/get/{config.XUI_INBOUND_ID}"
            )
            resp.raise_for_status()
            inbound_data = resp.json().get("obj", {})

            # Статистика может быть в clientStats
            client_stats = inbound_data.get("clientStats", [])
            for stat in client_stats:
                if stat.get("email") == uuid or stat.get("id") == uuid:
                    return {
                        "up": stat.get("up", 0),
                        "down": stat.get("down", 0),
                        "total": stat.get("total", 0),
                    }
            return None
        except Exception as e:
            logger.error(f"3x-ui: ошибка получения трафика - {e}")
            return None

    async def close(self):
        """Закрывает сессию."""
        if self._session:
            await self._session.aclose()
            self._session = None
            self._cookie = None


# Глобальный экземпляр
xray = XRayAPI()