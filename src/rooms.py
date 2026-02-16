from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[1]
ROOMS_FILE = BASE_DIR / "rooms.json"

_rooms_build_lock = asyncio.Lock()


def _normalize_rooms(rooms: Iterable[str]) -> list[str]:
    cleaned = {r.strip() for r in rooms if r and r.strip()}
    return sorted(cleaned)


def load_rooms(path: Path = ROOMS_FILE) -> list[str]:
    if not path.exists():
        return []
    try:
        return _normalize_rooms(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.exception("Failed to read rooms file: %s", path)
        return []


def save_rooms(rooms: Iterable[str], path: Path = ROOMS_FILE) -> None:
    path.write_text(
        json.dumps(_normalize_rooms(rooms), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def ensure_rooms(path: Path = ROOMS_FILE) -> list[str]:
    """
    Если rooms.json нет/пустой — строим его в runtime-контейнере, не ломая event loop.
    """
    rooms = load_rooms(path)
    if rooms:
        return rooms

    async with _rooms_build_lock:
        # повторная проверка внутри lock (если параллельно уже построили)
        rooms = load_rooms(path)
        if rooms:
            return rooms

        logger.warning("rooms.json not found or empty; building rooms via scripts/build_rooms.py ...")

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "scripts/build_rooms.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()

        if out:
            logger.info("build_rooms output:\n%s", out.decode("utf-8", errors="replace"))

        rooms = load_rooms(path)
        if not rooms:
            logger.warning("rooms.json still empty after build_rooms run")
        return rooms
