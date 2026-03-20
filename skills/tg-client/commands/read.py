"""Read messages from a chat."""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

from lib.client import TGClient
from telethon.errors import ChannelPrivateError
from telethon.tl.types import User


def _sender_name(sender) -> str:
    if sender is None:
        return "Unknown"
    if isinstance(sender, User):
        parts = [sender.first_name or "", sender.last_name or ""]
        return " ".join(p for p in parts if p) or "Unknown"
    return getattr(sender, "title", "Unknown")


async def run(args):
    async with TGClient() as client:
        try:
            entity = await client.get_entity(args.chat)
        except ChannelPrivateError:
            print(json.dumps({"error": "private_channel", "message": "Cannot access this channel"}), file=sys.stderr)
            sys.exit(1)
        except ValueError as e:
            print(json.dumps({"error": "not_found", "message": str(e)}), file=sys.stderr)
            sys.exit(1)

        kwargs = {"entity": entity, "limit": args.limit}
        if args.since:
            kwargs["offset_date"] = datetime.now(timezone.utc) - timedelta(hours=args.since)
            kwargs["reverse"] = True

        messages = []
        async for msg in client.iter_messages(**kwargs):
            sender = await msg.get_sender() if msg.sender_id else None
            messages.append({
                "message_id": msg.id,
                "sender_id": msg.sender_id,
                "sender_name": _sender_name(sender),
                "text": msg.text or "",
                "date": msg.date.isoformat() if msg.date else None,
                "reply_to_id": msg.reply_to.reply_to_msg_id if msg.reply_to else None,
            })

        print(json.dumps(messages, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Read messages from a chat")
    parser.add_argument("--chat", required=True, help="Chat ID, username, or @username")
    parser.add_argument("--limit", type=int, default=50, help="Max messages (default 50)")
    parser.add_argument("--since", type=float, default=None, help="Fetch messages from last N hours")
    args = parser.parse_args()

    # Parse chat ID — try numeric first
    try:
        args.chat = int(args.chat)
    except ValueError:
        pass

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
