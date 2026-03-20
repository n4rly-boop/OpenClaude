"""Send a message (PROTECTED — requires --confirm)."""

import argparse
import asyncio
import json
import sys

from lib.client import TGClient
from lib.db import StateDB
from telethon.errors import ChannelPrivateError


async def run(args):
    # Preview mode — no connection needed
    if not args.confirm:
        preview = {
            "action": "preview",
            "chat_id": args.chat,
            "text": args.text,
        }
        print(json.dumps(preview, ensure_ascii=False))
        return

    # Confirm mode — actually send
    print(
        "WARNING: Sending messages via user account may violate Telegram ToS "
        "and risk account ban. Proceeding with --confirm.",
        file=sys.stderr,
    )

    async with TGClient() as client:
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

        msg = await client.send_message(entity, args.text)

        # Audit log
        db = StateDB()
        db.log_action("send", args.chat, {"message_id": msg.id, "text": args.text})
        db.close()

        result = {
            "action": "sent",
            "message_id": msg.id,
            "chat_id": args.chat,
        }
        print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Send a message (PROTECTED)")
    parser.add_argument("--chat", required=True, help="Chat ID or @username")
    parser.add_argument("--text", required=True, help="Message text")
    parser.add_argument("--confirm", action="store_true", help="Actually send (without this, only preview)")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
