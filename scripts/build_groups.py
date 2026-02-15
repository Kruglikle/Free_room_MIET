import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.groups import GROUPS_FILE, refresh_groups


logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv(dotenv_path=ROOT / ".env")
    groups = await refresh_groups()
    if not groups:
        logger.error("Groups list is empty. Check network or parser.")
        return
    logger.info("Saved %d groups to %s", len(groups), GROUPS_FILE)


if __name__ == "__main__":
    asyncio.run(main())
