import subprocess
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def cleanup_system_logs():
    """Очищает системные логи (оставляет последние 14 дней)."""
    try:
        result = subprocess.run(
            ["journalctl", "--vacuum-time=14d"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            logger.info("✅ Системные логи очищены (оставлено 14 дней)")
            return True
        else:
            logger.error(f"❌ Ошибка очистки логов: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка при очистке логов: {e}")
        return False


async def cleanup_apt_cache():
    """Очищает кеш apt."""
    try:
        result = subprocess.run(
            ["apt-get", "clean"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            logger.info("✅ Кеш apt очищен")
            return True
        else:
            logger.error(f"❌ Ошибка очистки кеша apt: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка при очистке кеша apt: {e}")
        return False


async def cleanup_xui_logs():
    """Очищает логи 3x-ui (если они есть)."""
    try:
        # Проверяем, есть ли логи 3x-ui
        result = subprocess.run(
            ["find", "/var/log/x-ui", "-type", "f", "-name", "*.log", "-exec", "truncate", "-s", "0", "{}", "+"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            logger.info("✅ Логи 3x-ui очищены")
            return True
        else:
            logger.warning(f"⚠️ Не удалось очистить логи 3x-ui: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка при очистке логов 3x-ui: {e}")
        return False


async def full_cleanup():
    """Полная очистка системы (логи + кеш)."""
    logger.info("🧹 Начинаем плановую очистку системы...")
    
    results = []
    
    # 1. Очистка системных логов
    result = await cleanup_system_logs()
    results.append(("Системные логи", result))
    
    # 2. Очистка кеша apt
    result = await cleanup_apt_cache()
    results.append(("Кеш apt", result))
    
    # 3. Очистка логов 3x-ui
    result = await cleanup_xui_logs()
    results.append(("Логи 3x-ui", result))
    
    # Итог
    success_count = sum(1 for _, r in results if r)
    total_count = len(results)
    
    logger.info(f"🧹 Плановая очистка завершена: {success_count}/{total_count} задач выполнено")
    
    return results