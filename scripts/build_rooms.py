import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cache import TTLCache
from src.groups import load_groups, refresh_groups
from src.miet_api import MietAPI
from src.rooms import ROOMS_FILE, save_rooms
from src.scheduler import extract_room_name


logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv(dotenv_path=ROOT / ".env")
    groups = load_groups()
    if not groups:
        groups = await refresh_groups()
    if not groups:
        logger.error("Groups list is empty. Run build_groups.py first.")
        return

    cache_ttl = int(os.getenv("MIET_CACHE_TTL", "120"))
    max_concurrency = int(os.getenv("MIET_MAX_CONCURRENCY", "10"))
    api = MietAPI(concurrency=max_concurrency, cache=TTLCache(ttl_seconds=cache_ttl))
    schedules = await api.fetch_all(groups)

    rooms: set[str] = set()
    for schedule in schedules:
        if not schedule:
            continue
        for item in schedule.get("Data", []):
            name = extract_room_name(item)
            if name:
                rooms.add(name)

    if not rooms:
        logger.error("No rooms found. Check API response.")
        return

    save_rooms(sorted(rooms))
    logger.info("Saved %d rooms to %s", len(rooms), ROOMS_FILE)


if __name__ == "__main__":
    asyncio.run(main())
