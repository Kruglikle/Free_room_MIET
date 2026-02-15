from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Any, Iterable

import aiohttp


SCHEDULE_URL = "https://miet.ru/schedule/data"
SCHEDULE_GROUPS_URL = "https://miet.ru/schedule/groups"
SCHEDULE_PAGE_URL = "https://miet.ru/schedule"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

logger = logging.getLogger(__name__)


class MietAPI:
    def __init__(
        self,
        *,
        concurrency: int = 10,
        timeout: aiohttp.ClientTimeout | None = None,
        headers: dict[str, str] | None = None,
        force_ipv4: bool | None = None,
        disable_ssl_verify: bool | None = None,
        local_addr: tuple[str, int] | None = None,
        cache: Any | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._timeout = timeout or build_timeout()
        self._headers = headers or build_headers()
        self._force_ipv4 = force_ipv4 if force_ipv4 is not None else env_flag("MIET_FORCE_IPV4", True)
        self._disable_ssl = (
            disable_ssl_verify if disable_ssl_verify is not None else env_flag("MIET_DISABLE_SSL_VERIFY", False)
        )
        self._local_addr = local_addr if local_addr is not None else build_local_addr()
        self._cache = cache

    async def _post_schedule(self, session: aiohttp.ClientSession, group: str) -> dict[str, Any] | None:
        async with self._semaphore:
            try:
                async with session.post(SCHEDULE_URL, data={"group": group}) as resp:
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except Exception:
                logger.exception("Failed to fetch schedule for group=%s", group)
                return None

    async def _get_schedule(self, session: aiohttp.ClientSession, group: str) -> dict[str, Any] | None:
        if not self._cache:
            return await self._post_schedule(session, group)

        async def factory() -> dict[str, Any] | None:
            return await self._post_schedule(session, group)

        return await self._cache.get_or_set(group, factory)

    async def fetch_one(self, group: str) -> dict[str, Any] | None:
        async with aiohttp.ClientSession(
            timeout=self._timeout,
            headers=self._headers,
            connector=build_connector(self._force_ipv4, self._disable_ssl, self._local_addr),
            trust_env=True,
        ) as session:
            return await self._get_schedule(session, group)

    async def fetch_all(self, groups: Iterable[str]) -> list[dict[str, Any] | None]:
        groups_list = list(groups)
        if not groups_list:
            return []
        async with aiohttp.ClientSession(
            timeout=self._timeout,
            headers=self._headers,
            connector=build_connector(self._force_ipv4, self._disable_ssl, self._local_addr),
            trust_env=True,
        ) as session:
            tasks = [self._get_schedule(session, group) for group in groups_list]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        schedules: list[dict[str, Any] | None] = []
        for group, result in zip(groups_list, results):
            if isinstance(result, Exception):
                logger.exception("Schedule task failed for group=%s", group, exc_info=result)
                schedules.append(None)
            else:
                schedules.append(result)
        return schedules


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no"}


def build_timeout() -> aiohttp.ClientTimeout:
    total = int(os.getenv("MIET_TIMEOUT", "45"))
    return aiohttp.ClientTimeout(total=total)


def build_headers() -> dict[str, str]:
    return {"User-Agent": os.getenv("MIET_USER_AGENT", DEFAULT_USER_AGENT)}


def build_connector(
    force_ipv4: bool,
    disable_ssl: bool | None = None,
    local_addr: tuple[str, int] | None = None,
) -> aiohttp.TCPConnector:
    if disable_ssl is None:
        disable_ssl = env_flag("MIET_DISABLE_SSL_VERIFY", False)
    family = socket.AF_INET if force_ipv4 else socket.AF_UNSPEC
    ssl_setting = False if disable_ssl else None
    return aiohttp.TCPConnector(family=family, ssl=ssl_setting, local_addr=local_addr)


def build_local_addr() -> tuple[str, int] | None:
    value = os.getenv("MIET_LOCAL_ADDR")
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if ":" in value:
        host, port_str = value.split(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 0
        return host, port
    return value, 0
