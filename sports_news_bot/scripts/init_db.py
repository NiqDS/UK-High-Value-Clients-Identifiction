#!/usr/bin/env python3
"""Standalone database initialisation script.

Run from the project root:
    python scripts/init_db.py
"""
import asyncio
import sys
from pathlib import Path

# Allow imports from project root when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.database import init_database


async def main() -> None:
    print("Initialising database…")
    await init_database()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
