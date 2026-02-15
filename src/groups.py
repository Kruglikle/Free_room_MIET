from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Iterable

import aiohttp
from bs4 import BeautifulSoup

from .miet_api import (
    SCHEDULE_PAGE_URL,
    SCHEDULE_GROUPS_URL,
    SCHEDULE_URL,
    build_connector,
    build_headers,
    build_local_addr,
    build_timeout,
    env_flag,
)


logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[1]
GROUPS_FILE = BASE_DIR / "groups.json"

GROUP_RE = re.compile(r"[A-ZА-Я]{1,6}-\d{2}[A-ZА-Я]?")


def _normalize_groups(groups: Iterable[str]) -> list[str]:
    cleaned = {g.strip() for g in groups if g and g.strip()}
    return sorted(cleaned)


def _extract_groups_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    groups: list[str] = []
    for select in soup.find_all("select"):
        select_id = (select.get("id") or "").lower()
        select_name = (select.get("name") or "").lower()
        if "group" not in select_id and "group" not in select_name:
            continue
        for option in select.find_all("option"):
            value = (option.get("value") or option.get_text() or "").strip()
            if not value or "группа" in value.lower():
                continue
            groups.append(value)

    if not groups:
        groups.extend(GROUP_RE.findall(html))

    return _normalize_groups(groups)


async def _fetch_schedule_page(session: aiohttp.ClientSession) -> list[str]:
    try:
        async with session.get(SCHEDULE_PAGE_URL) as resp:
            resp.raise_for_status()
            html = await resp.text()
            return _extract_groups_from_html(html)
    except Exception:
        logger.exception("Failed to load schedule page for groups")
        return []


async def _fetch_groups_api(session: aiohttp.ClientSession) -> list[str]:
    try:
        async with session.post(SCHEDULE_GROUPS_URL) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
            if isinstance(data, list):
                return _normalize_groups(data)
    except Exception:
        logger.exception("Failed to load groups from API")
    return []


def _candidate_groups() -> list[str]:
    prefixes = os.getenv("MIET_GROUP_PATTERNS", "ПМ,ИВТ,КТС,БИ,ИС,ИТ").split(",")
    suffixes = os.getenv("MIET_GROUP_SUFFIXES", ",А,Б").split(",")
    years = range(10, 60)
    candidates: list[str] = []
    for prefix in (p.strip() for p in prefixes if p.strip()):
        for year in years:
            base = f"{prefix}-{year:02d}"
            for suffix in suffixes:
                candidates.append(f"{base}{suffix.strip()}")
    limit = int(os.getenv("MIET_GROUP_GUESS_LIMIT", "300"))
    return candidates[:limit]


async def _guess_groups(session: aiohttp.ClientSession) -> list[str]:
    semaphore = asyncio.Semaphore(10)
    candidates = _candidate_groups()
    found: list[str] = []

    async def check(group: str) -> None:
        async with semaphore:
            try:
                async with session.post(SCHEDULE_URL, data={"group": group}) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json(content_type=None)
                    if isinstance(data, dict) and data.get("Times"):
                        found.append(group)
            except Exception:
                return

    await asyncio.gather(*(check(group) for group in candidates))
    return _normalize_groups(found)


async def fetch_groups(allow_guess: bool = True) -> list[str]:
    async with aiohttp.ClientSession(
        timeout=build_timeout(),
        headers=build_headers(),
        connector=build_connector(
            env_flag("MIET_FORCE_IPV4", True),
            env_flag("MIET_DISABLE_SSL_VERIFY", False),
            build_local_addr(),
        ),
        trust_env=True,
    ) as session:
        groups = await _fetch_groups_api(session)
        if not groups:
            groups = await _fetch_schedule_page(session)
        if groups:
            return groups
        if allow_guess:
            return await _guess_groups(session)
        return []


def load_groups(path: Path = GROUPS_FILE) -> list[str]:
    if not path.exists():
        return []
    try:
        return _normalize_groups(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.exception("Failed to read groups file: %s", path)
        return []


def save_groups(groups: Iterable[str], path: Path = GROUPS_FILE) -> None:
    path.write_text(json.dumps(_normalize_groups(groups), ensure_ascii=False, indent=2), encoding="utf-8")


async def refresh_groups(path: Path = GROUPS_FILE, allow_guess: bool = True) -> list[str]:
    groups = await fetch_groups(allow_guess=allow_guess)
    if groups:
        save_groups(groups, path)
    return groups
