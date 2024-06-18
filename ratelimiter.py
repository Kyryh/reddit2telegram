from typing import Any, Callable, Coroutine
from telegram.ext import BaseRateLimiter
from telegram.error import RetryAfter
import asyncio
import logging

logger = logging.getLogger("ratelimiter")


class RateLimiter(BaseRateLimiter):
    async def initialize(self) -> Coroutine[Any, Any, None]:
        pass
    
    async def shutdown(self) -> Coroutine[Any, Any, None]:
        pass
    
    async def process_request(self, callback: Callable[..., Coroutine[Any, Any, bool | dict[str] | list[dict[str]]]], args: Any, kwargs: dict[str], endpoint: str, data: dict[str], rate_limit_args: Any | None) -> Coroutine[Any, Any, bool | dict[str] | list[dict[str]]]:
        while True:
            try:
                return await callback(*args, **kwargs)
            except RetryAfter as e:
                logger.warning(e)
                await asyncio.sleep(e.retry_after)
