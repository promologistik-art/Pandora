import os
import subprocess
import logging
from datetime import datetime
from config import config

logger = logging.getLogger(__name__)

BACKUP_DIR = "/app/backups"


async def create_backup() -> str | None:
    """Создаёт резервную копию базы данных."""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"backup_{timestamp}.sql")
        
        # Парсим DATABASE_URL
        db_url = config.DATABASE_URL
        parts = db_url.split("//")[1].split("@")
        user_pass = parts[0].split(":")
        host_db = parts[1].split("/")
        
        user = user_pass[0]
        password = user_pass[1] if len(user_pass) > 1 else ""
        host = host_db[0].split(":")[0]
        port = host_db[0].split(":")[1] if ":" in host_db[0] else "5432"
        dbname = host_db[1]
        
        # Формируем команду pg_dump
        cmd = [
            "pg_dump",
            f"--host={host}",
            f"--port={port}",
            f"--username={user}",
            f"--dbname={dbname}",
            "--format=plain",
            "--clean",
            "--if-exists",
            f"--file={backup_file}"
        ]
        
        env = os.environ.copy()
        env["PGPASSWORD"] = password
        
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        
        if result.returncode == 0:
            # Сжимаем бэкап
            import gzip
            with open(backup_file, 'rb') as f_in:
                with gzip.open(f"{backup_file}.gz", 'wb') as f_out:
                    f_out.write(f_in.read())
            os.remove(backup_file)
            
            logger.info(f"✅ Бэкап создан: {backup_file}.gz")
            return f"{backup_file}.gz"
        else:
            logger.error(f"❌ Ошибка бэкапа: {result.stderr}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Ошибка при создании бэкапа: {e}")
        return None


async def cleanup_old_backups(keep_last: int = 7):
    """Удаляет старые бэкапы, оставляя только последние N."""
    try:
        if not os.path.exists(BACKUP_DIR):
            return
            
        files = sorted([
            f for f in os.listdir(BACKUP_DIR)
            if f.endswith(".sql.gz")
        ])
        
        if len(files) > keep_last:
            for f in files[:-keep_last]:
                os.remove(os.path.join(BACKUP_DIR, f))
                logger.info(f"Удалён старый бэкап: {f}")
                
    except Exception as e:
        logger.error(f"Ошибка при очистке бэкапов: {e}")