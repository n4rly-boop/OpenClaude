"""Collect recent messages across chats and write to JSON file."""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.client import TGClient
from lib.db import StateDB
from telethon.errors import ChannelPrivateError
from telethon.tl.types import Channel, Chat, User


def _chat_type(entity) -> str:
    if isinstance(entity, User):
        return "dm"
    if isinstance(entity, Channel):
        return "channel" if entity.broadcast else "group"
    if isinstance(entity, Chat):
        return "group"
    return "unknown"


def _chat_name(dialog) -> str:
    entity = dialog.entity
    if isinstance(entity, User):
        parts = [entity.first_name or "", entity.last_name or ""]
        return " ".join(p for p in parts if p) or "Unknown"
    return dialog.title or "Unknown"


def _build_link(entity, message_id: int) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    chat_id = getattr(entity, "id", 0)
    return f"https://t.me/c/{chat_id}/{message_id}"


async def run(args):
    db = StateDB()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=args.since_minutes)
    target_chats = None
    if args.chats and args.chats != "all":
        raw = args.chats.split(",")
        target_chats = set()
        for c in raw:
            c = c.strip()
            try:
                target_chats.add(int(c))
            except ValueError:
                target_chats.add(c)

    messages = []
    chats_scanned = 0

    async with TGClient() as client:
        me = await client.get_me()

        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            ctype = _chat_type(entity)

            # If specific chats requested, filter
            if target_chats is not None:
                if dialog.id not in target_chats:
                    username = getattr(entity, "username", None)
                    if not username or username not in target_chats:
                        if f"@{username}" not in target_chats:
                            continue
            else:
                # Default: only groups and channels
                if ctype not in ("group", "channel"):
                    continue

            chat_name_str = _chat_name(dialog)
            last_id = db.get_last_message_id(dialog.id)
            max_id = last_id

            try:
                async for msg in client.iter_messages(
                    entity, offset_date=cutoff, reverse=True, limit=200
                ):
                    if msg.id <= last_id:
                        continue
                    if msg.id > max_id:
                        max_id = msg.id
                    if not msg.text:
                        continue
                    if msg.sender_id == me.id:
                        continue

                    messages.append({
                        "chat_id": dialog.id,
                        "chat_name": chat_name_str,
                        "message_id": msg.id,
                        "sender_id": msg.sender_id,
                        "text": msg.text,
                        "date": msg.date.isoformat() if msg.date else None,
                        "link": _build_link(entity, msg.id),
                    })
            except (ChannelPrivateError, Exception):
                continue

            if max_id > last_id:
                db.set_last_message_id(dialog.id, max_id)
            chats_scanned += 1

    db.close()

    # Write output
    output_path = args.output
    if not output_path:
        import os
        ws = os.environ.get("OPENCLAUDE_WORKSPACE_DIR", "/tmp")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_path = str(Path(ws) / "temp" / f"messages-{ts}.json")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)

    result = {
        "output_file": output_path,
        "message_count": len(messages),
        "chats_scanned": chats_scanned,
    }
    print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Monitor chats and collect messages")
    parser.add_argument("--since-minutes", type=int, default=12, help="Fetch messages from last N minutes")
    parser.add_argument("--chats", default="all", help="all or comma-separated chat IDs/usernames")
    parser.add_argument("--output", default=None, help="Output JSON file path")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
