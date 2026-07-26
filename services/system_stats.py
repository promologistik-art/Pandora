import psutil
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def get_system_stats() -> dict | None:
    """Получить статистику системы."""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        
        days = uptime.days
        hours = uptime.seconds // 3600
        minutes = (uptime.seconds % 3600) // 60
        
        if days > 0:
            uptime_str = f"{days}д {hours}ч {minutes}м"
        else:
            uptime_str = f"{hours}ч {minutes}м"
        
        return {
            "cpu": cpu_percent,
            "ram": memory.percent,
            "ram_used": memory.used // (1024 * 1024),
            "ram_total": memory.total // (1024 * 1024),
            "disk": disk.percent,
            "disk_used": disk.used // (1024 * 1024 * 1024),
            "disk_total": disk.total // (1024 * 1024 * 1024),
            "uptime": uptime_str,
        }
    except Exception as e:
        logger.error(f"Ошибка получения системной статистики: {e}")
        return None