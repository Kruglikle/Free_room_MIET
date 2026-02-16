from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path
from typing import Iterable


logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[1]
ROOMS_FILE = BASE_DIR / "rooms.json"


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
    path.write_text(json.dumps(_normalize_rooms(rooms), ensure_ascii=False, indent=2), encoding="utf-8")

def ensure_rooms(path: Path = ROOMS_FILE) -> list[str]:
    rooms = load_rooms(path)
    if rooms:
        return rooms

    logger.warning("rooms.json not found or empty; building rooms now...")

    # запускаем сбор комнат как модуль, чтобы не зависеть от pre-deploy
    from scripts.build_rooms import main as build_rooms_main

    try:
        asyncio.run(build_rooms_main())
    except RuntimeError:
        # если мы уже внутри event loop (например aiogram), делаем иначе
        loop = asyncio.get_event_loop()
        loop.run_until_complete(build_rooms_main())

    return load_rooms(path)

