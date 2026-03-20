"""Search messages by text."""

import argparse
import asyncio
import json
import sys

from lib.client import TGClient
from telethon.errors import ChannelPrivateError


def _build_link(entity, message_id: int) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    chat_id = getattr(entity, "id", 0)
    return f"https://t.me/c/{chat_id}/{message_id}"


async def run(args):
    async with TGClient() as client:
        results = []

        if args.chat:
            # Search in specific chat
            try:
                try:
                    chat_id = int(args.chat)
                except ValueError:
                    chat_id = args.chat
                entity = await client.get_entity(chat_id)
            except ChannelPrivateError:
                print(json.dumps({"error": "private_channel", "message": "Cannot access this channel"}), file=sys.stderr)
                sys.exit(1)
            except ValueError as e:
                print(json.dumps({"error": "not_found", "message": str(e)}), file=sys.stderr)
                sys.exit(1)

            async for msg in client.iter_messages(entity, search=args.query, limit=args.limit):
                if not msg.text:
                    continue
                results.append({
                    "chat_id": getattr(entity, "id", None),
                    "chat_name": getattr(entity, "title", getattr(entity, "first_name", "Unknown")),
                    "message_id": msg.id,
                    "sender_id": msg.sender_id,
                    "text": msg.text,
                    "date": msg.date.isoformat() if msg.date else None,
                    "link": _build_link(entity, msg.id),
                })
        else:
            # Search across all dialogs
            remaining = args.limit
            async for dialog in client.iter_dialogs():
                if remaining <= 0:
                    break
                entity = dialog.entity
                try:
                    async for msg in client.iter_messages(entity, search=args.query, limit=min(remaining, 20)):
                        if not msg.text:
                            continue
                        chat_name = dialog.title or "Unknown"
                        results.append({
                            "chat_id": dialog.id,
                            "chat_name": chat_name,
                            "message_id": msg.id,
                            "sender_id": msg.sender_id,
                            "text": msg.text,
                            "date": msg.date.isoformat() if msg.date else None,
                            "link": _build_link(entity, msg.id),
                        })
                        remaining -= 1
                        if remaining <= 0:
                            break
                except (ChannelPrivateError, Exception):
                    continue

        print(json.dumps(results, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Search messages by text")
    parser.add_argument("--query", required=True, help="Search text")
    parser.add_argument("--chat", default=None, help="Chat ID or username (searches all if omitted)")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
