from typing import Any, Callable, Coroutine, Dict, List
from telegram.ext import BaseRateLimiter
from telegram.error import RetryAfter
import asyncio
import logging

logger = logging.getLogger("ratelimiter")


class RateLimiter(BaseRateLimiter):
    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def process_request(
        self,
        callback: Callable[
            ..., Coroutine[Any, Any, bool | Dict[str, Any] | List[Dict[str, Any]]]
        ],
        args: Any,
        kwargs: Dict[str, Any],
        endpoint: str,
        data: Dict[str, Any],
        rate_limit_args: Any | None,
    ) -> bool | Dict[str, Any] | List[Dict[str, Any]]:
        while True:
            try:
                return await callback(*args, **kwargs)
            except RetryAfter as e:
                logger.warning(e)
                await asyncio.sleep(e.retry_after)
