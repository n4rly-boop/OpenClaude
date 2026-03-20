"""Get channel statistics and info."""

import argparse
import asyncio
import json
import sys

from lib.client import TGClient
from telethon.errors import ChannelPrivateError
from telethon.tl.functions.channels import GetFullChannelRequest


async def run(args):
    async with TGClient() as client:
        try:
            try:
                channel_id = int(args.channel)
            except ValueError:
                channel_id = args.channel
            entity = await client.get_entity(channel_id)
        except ChannelPrivateError:
            print(json.dumps({"error": "private_channel", "message": "Cannot access this channel"}), file=sys.stderr)
            sys.exit(1)
        except ValueError as e:
            print(json.dumps({"error": "not_found", "message": str(e)}), file=sys.stderr)
            sys.exit(1)

        # Get full channel info
        try:
            full = await client(GetFullChannelRequest(channel=entity))
            full_chat = full.full_chat
        except Exception as e:
            print(json.dumps({"error": "api_error", "message": str(e)}), file=sys.stderr)
            sys.exit(1)

        # Fetch last 10 posts for avg views
        recent_posts = []
        total_views = 0
        view_count = 0
        async for msg in client.iter_messages(entity, limit=10):
            views = msg.views or 0
            text_preview = (msg.text or "")[:200] if msg.text else ""
            recent_posts.append({
                "message_id": msg.id,
                "date": msg.date.isoformat() if msg.date else None,
                "views": views,
                "text_preview": text_preview,
            })
            total_views += views
            view_count += 1

        avg_views = total_views // view_count if view_count > 0 else 0

        output = {
            "channel_id": entity.id,
            "title": getattr(entity, "title", ""),
            "username": getattr(entity, "username", None),
            "description": getattr(full_chat, "about", "") or "",
            "subscribers": getattr(full_chat, "participants_count", None),
            "is_verified": getattr(entity, "verified", False),
            "is_scam": getattr(entity, "scam", False),
            "avg_views_last10": avg_views,
            "recent_posts": recent_posts,
        }
        print(json.dumps(output, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Get channel info and statistics")
    parser.add_argument("--channel", required=True, help="Channel ID or @username")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
