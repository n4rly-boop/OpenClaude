"""Get user profile info."""

import argparse
import asyncio
import json
import sys

from lib.client import TGClient
from telethon.tl.functions.users import GetFullUserRequest


async def run(args):
    async with TGClient() as client:
        try:
            try:
                user_id = int(args.user)
            except ValueError:
                user_id = args.user
            entity = await client.get_entity(user_id)
        except ValueError as e:
            print(json.dumps({"error": "not_found", "message": str(e)}), file=sys.stderr)
            sys.exit(1)

        try:
            full_result = await client(GetFullUserRequest(id=entity))
            full_user = full_result.full_user
        except Exception as e:
            print(json.dumps({"error": "api_error", "message": str(e)}), file=sys.stderr)
            sys.exit(1)

        # Personal channel
        personal_channel_id = None
        if hasattr(full_user, "personal_channel_id") and full_user.personal_channel_id:
            personal_channel_id = full_user.personal_channel_id

        output = {
            "user_id": entity.id,
            "first_name": entity.first_name or "",
            "last_name": entity.last_name or "",
            "username": entity.username or None,
            "phone": entity.phone if hasattr(entity, "phone") and entity.phone else None,
            "bio": getattr(full_user, "about", "") or "",
            "is_bot": getattr(entity, "bot", False),
            "is_verified": getattr(entity, "verified", False),
            "personal_channel_id": personal_channel_id,
            "mutual_contact": getattr(entity, "mutual_contact", False),
        }
        print(json.dumps(output, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Get user profile info")
    parser.add_argument("--user", required=True, help="User ID or @username")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
