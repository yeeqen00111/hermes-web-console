import asyncio
import sys

from chat import ChatManager
from config import hc


async def main():
    manager = ChatManager(hc)
    text = sys.argv[1] if len(sys.argv) > 1 else "hi"
    stored_id = sys.argv[2] if len(sys.argv) > 2 else None
    profile = sys.argv[3] if len(sys.argv) > 3 else None
    try:
        session, queue = await manager.start_turn(text, stored_id, profile)
        while True:
            name, event = await queue.get()
            print(f"[{name}] {event}")
            if name in ("message.complete", "chat.error"):
                break
        await session.task
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
