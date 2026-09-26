import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import get_settings
from app.data.fuyao import FuyaoClient


async def main() -> None:
    get_settings.cache_clear()
    client = FuyaoClient()
    result = await client.probe()
    print("mode:", result.get("mode"))
    print("ok:", result.get("ok"))
    print("detail:", result.get("detail"))


if __name__ == "__main__":
    asyncio.run(main())
