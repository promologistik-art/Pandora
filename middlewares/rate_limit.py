import time
from typing import Dict, Optional
from aiogram import types
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from config import config


class RateLimitMiddleware(BaseMiddleware):
    """Middleware для ограничения количества запросов от пользователей."""
    
    def __init__(self, limit: int = 5, period: int = 10):
        """
        Args:
            limit: Максимальное количество запросов за период
            period: Период в секундах
        """
        self.limit = limit
        self.period = period
        self.user_requests: Dict[int, list] = {}

    async def __call__(self, handler, event: types.Update, data: dict):
        user_id = None
        
        if event.message:
            user_id = event.message.from_user.id
        elif event.callback_query:
            user_id = event.callback_query.from_user.id
        else:
            return await handler(event, data)
        
        # Админам не ограничиваем
        if user_id in config.ADMIN_IDS:
            return await handler(event, data)
        
        current_time = time.time()
        
        # Очищаем старые записи
        if user_id in self.user_requests:
            self.user_requests[user_id] = [
                t for t in self.user_requests[user_id]
                if current_time - t < self.period
            ]
        else:
            self.user_requests[user_id] = []
        
        # Проверяем лимит
        if len(self.user_requests[user_id]) >= self.limit:
            if event.message:
                await event.message.answer(
                    "⏳ <b>Слишком много запросов!</b>\n"
                    "Пожалуйста, подождите немного и попробуйте снова."
                )
            elif event.callback_query:
                await event.callback_query.answer(
                    "⏳ Слишком много запросов! Подождите немного.",
                    show_alert=True
                )
            return
        
        # Добавляем текущий запрос
        self.user_requests[user_id].append(current_time)
        
        return await handler(event, data)