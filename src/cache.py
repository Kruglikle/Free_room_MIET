from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable


class TTLCache:
    def __init__(self, ttl_seconds: int = 120) -> None:
        self._ttl = ttl_seconds
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        now = time.time()
        async with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at < now:
                self._data.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        expires_at = time.time() + self._ttl
        async with self._lock:
            self._data[key] = (expires_at, value)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()

    async def get_or_set(self, key: str, factory: Callable[[], Awaitable[Any]]) -> Any | None:
        cached = await self.get(key)
        if cached is not None:
            return cached
        value = await factory()
        if value is not None:
            await self.set(key, value)
        return value
