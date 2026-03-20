"""List accessible chats/channels."""

import argparse
import asyncio
import json

from lib.client import TGClient
from telethon.tl.types import Channel, Chat, User


def _chat_type(entity) -> str:
    if isinstance(entity, User):
        return "dm"
    if isinstance(entity, Channel):
        return "channel" if entity.broadcast else "group"
    if isinstance(entity, Chat):
        return "group"
    return "unknown"


async def run(args):
    async with TGClient() as client:
        results = []
        count = 0
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            ctype = _chat_type(entity)

            if args.type != "all" and ctype != args.type:
                continue

            title = dialog.title or ""
            if isinstance(entity, User):
                parts = [entity.first_name or "", entity.last_name or ""]
                title = " ".join(p for p in parts if p) or "Unknown"

            username = getattr(entity, "username", None)
            members = getattr(entity, "participants_count", None)

            results.append({
                "chat_id": dialog.id,
                "title": title,
                "type": ctype,
                "username": username,
                "members_count": members,
            })

            count += 1
            if count >= args.limit:
                break

        print(json.dumps(results, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="List accessible chats")
    parser.add_argument("--type", default="all", choices=["all", "group", "channel", "dm"])
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
