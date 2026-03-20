"""Get comments on a channel post."""

import argparse
import asyncio
import json
import sys

from lib.client import TGClient
from telethon.errors import ChannelPrivateError, MsgIdInvalidError
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

        comments = []
        try:
            async for msg in client.iter_messages(
                entity, reply_to=args.post_id, limit=args.limit
            ):
                sender = await msg.get_sender() if msg.sender_id else None
                sender_username = None
                if isinstance(sender, User):
                    sender_username = sender.username

                comments.append({
                    "message_id": msg.id,
                    "sender_id": msg.sender_id,
                    "sender_name": _sender_name(sender),
                    "sender_username": sender_username,
                    "text": msg.text or "",
                    "date": msg.date.isoformat() if msg.date else None,
                    "reply_to_id": msg.reply_to.reply_to_msg_id if msg.reply_to else None,
                })
        except MsgIdInvalidError:
            print(json.dumps({"error": "invalid_post", "message": f"Post {args.post_id} not found or has no comments"}), file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            # Some channels don't support comments
            err_str = str(e).lower()
            if "discussion" in err_str or "comment" in err_str:
                # No discussion group linked — return empty
                pass
            else:
                print(json.dumps({"error": "api_error", "message": str(e)}), file=sys.stderr)
                sys.exit(1)

        print(json.dumps(comments, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Get comments on a channel post")
    parser.add_argument("--channel", required=True, help="Channel ID or @username")
    parser.add_argument("--post_id", "--post-id", type=int, required=True, help="Message/post ID")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
