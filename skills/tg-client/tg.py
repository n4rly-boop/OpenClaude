#!/usr/bin/env python3
"""
tg.py — Unified CLI entry point for tg-client.

Usage: tg.py <command> [args]

Commands:
  read          Read messages from a chat
  list          List accessible chats/channels
  search        Search messages by text
  get-similar   Get similar channels (TG recommendations)
  channel-info  Get channel statistics
  monitor       Collect recent messages across chats -> JSON file
  get-comments  Get comments on a channel post
  get-user      Get user profile info
  send          Send a message (PROTECTED — requires --confirm)

All output to stdout as JSON. Errors to stderr as {"error": "...", "message": "..."}.
"""

import json
import os
import sys
from pathlib import Path

# Load workspace .env before anything else
workspace_dir = os.environ.get("OPENCLAUDE_WORKSPACE_DIR", "")
if workspace_dir:
    try:
        from dotenv import load_dotenv
        env_path = Path(workspace_dir) / ".env"
        if env_path.exists():
            load_dotenv(str(env_path), override=False)
    except ImportError:
        pass

# Add skill directory to path for imports
SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))

COMMANDS = {
    "read": "commands.read",
    "list": "commands.list",
    "search": "commands.search",
    "get-similar": "commands.get_similar",
    "channel-info": "commands.channel_info",
    "monitor": "commands.monitor",
    "get-comments": "commands.get_comments",
    "get-user": "commands.get_user",
    "send": "commands.send",
}


def print_usage():
    print(__doc__.strip(), file=sys.stderr)
    sys.exit(1)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print_usage()

    command = sys.argv[1]
    if command not in COMMANDS:
        print(
            json.dumps({"error": "unknown_command", "message": f"Unknown command: {command}"}),
            file=sys.stderr,
        )
        sys.exit(1)

    # Remove command name from argv so argparse in submodules works
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    try:
        module = __import__(COMMANDS[command], fromlist=["main"])
        module.main()
    except SystemExit:
        raise
    except Exception as e:
        print(
            json.dumps({"error": "unexpected", "message": str(e)}),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
