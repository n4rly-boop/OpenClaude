"""Get similar channels (Telegram recommendations)."""

import argparse
import asyncio
import json
import sys

from lib.client import TGClient
from telethon.errors import ChannelPrivateError
from telethon.tl.functions.channels import GetChannelRecommendationsRequest


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

        similar = []
        try:
            result = await client(GetChannelRecommendationsRequest(channel=entity))
            for chat in result.chats:
                similar.append({
                    "channel_id": chat.id,
                    "title": chat.title,
                    "username": getattr(chat, "username", None),
                    "subscribers": getattr(chat, "participants_count", None),
                })
        except Exception:
            # TG may not return recommendations for some channels — not an error
            pass

        output = {
            "channel_id": entity.id,
            "title": getattr(entity, "title", ""),
            "similar": similar,
        }
        print(json.dumps(output, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Get similar channels")
    parser.add_argument("--channel", required=True, help="Channel ID or @username")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
