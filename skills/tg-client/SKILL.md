# Skill: tg-client

## Purpose
Generic Telegram client interface via Telethon (user account API).
Provides atomic commands for reading, searching, monitoring, and analyzing Telegram data.
An Opus coordinator agent uses these commands to fulfill user requests.

## Safety Warning — send command
Using `send` sends messages FROM THE USER'S PERSONAL TELEGRAM ACCOUNT.
- Only use with EXPLICIT user permission
- Mass sending or spam WILL result in account ban
- Every send is logged to audit_log in tg-client.db
- Always requires `--confirm` flag
- Without `--confirm`, the command only returns a preview (no message sent)

## Usage
All commands are invoked via:
```bash
OPENCLAUDE_WORKSPACE_DIR=/path/to/workspace python3 /root/OpenClaude/skills/tg-client/tg.py <command> [args]
```

Output is JSON on stdout. Errors on stderr as `{"error": "...", "message": "..."}`.

## Environment Variables (from workspace .env)
- `TG_MONITOR_API_ID` — Telegram API ID
- `TG_MONITOR_API_HASH` — Telegram API hash
- `TG_MONITOR_PHONE` — Phone number of account
- `TG_MONITOR_ADMIN_CHAT_ID` — Admin chat for monitor notifications (optional)
- `TG_MONITOR_TARGET_CHAT` — Default target chat for notifications (optional)
- `TG_MONITOR_TARGET_THREAD` — Thread ID for notifications (optional)
- `OPENCLAUDE_WORKSPACE_DIR` — Workspace directory (must be set; session files, DB, and locks live here)

## Commands

### read — Read messages from a chat
```bash
tg.py read --chat CHAT_ID [--limit 50] [--since HOURS]
```
- `--chat` — Chat ID (numeric), username, or @username
- `--limit` — Max messages to fetch (default: 50)
- `--since` — Only messages from last N hours (optional; uses reverse chronological without it)

Output:
```json
[
  {
    "message_id": 12345,
    "sender_id": 987654,
    "sender_name": "John Doe",
    "text": "Hello world",
    "date": "2026-03-20T10:30:00+00:00",
    "reply_to_id": null
  }
]
```

### list — List accessible chats
```bash
tg.py list [--type all|group|channel|dm] [--limit 100]
```
- `--type` — Filter by chat type (default: all)
- `--limit` — Max chats to return (default: 100)

Output:
```json
[
  {
    "chat_id": -1001234567890,
    "title": "Dev Chat",
    "type": "group",
    "username": "devchat",
    "members_count": 1500
  }
]
```

### search — Search messages by text
```bash
tg.py search --query TEXT [--chat CHAT_ID] [--limit 50]
```
- `--query` — Search text (required)
- `--chat` — Search in specific chat only (optional; searches all dialogs if omitted)
- `--limit` — Max results (default: 50)

Output:
```json
[
  {
    "chat_id": -1001234567890,
    "chat_name": "Dev Chat",
    "message_id": 12345,
    "sender_id": 987654,
    "text": "matching message text...",
    "date": "2026-03-20T10:30:00+00:00",
    "link": "https://t.me/devchat/12345"
  }
]
```

Note: Searching across all dialogs iterates chats one by one (up to 20 results per chat). For large accounts this can be slow. Prefer `--chat` when you know the target.

### get-similar — Get similar channels (TG recommendations)
```bash
tg.py get-similar --channel CHANNEL_ID_OR_USERNAME
```

Output:
```json
{
  "channel_id": 1234567890,
  "title": "My Channel",
  "similar": [
    {
      "channel_id": 9876543210,
      "title": "Similar Channel",
      "username": "similarchan",
      "subscribers": 50000
    }
  ]
}
```

**Important:** Returns empty `similar: []` (not an error) if Telegram has no recommendations for a channel. This is normal for small or private channels.

### channel-info — Channel statistics
```bash
tg.py channel-info --channel CHANNEL_ID_OR_USERNAME
```

Output:
```json
{
  "channel_id": 1234567890,
  "title": "My Channel",
  "username": "mychan",
  "description": "Channel about things",
  "subscribers": 25000,
  "is_verified": false,
  "is_scam": false,
  "avg_views_last10": 3500,
  "recent_posts": [
    {
      "message_id": 999,
      "date": "2026-03-20T10:00:00+00:00",
      "views": 4200,
      "text_preview": "First 200 chars of post..."
    }
  ]
}
```

Fetches the last 10 posts to compute `avg_views_last10`.

### monitor — Collect recent messages to JSON file
```bash
tg.py monitor [--since-minutes 12] [--chats all|CHAT1,CHAT2] [--output FILE]
```
- `--since-minutes` — Time window (default: 12)
- `--chats` — `all` (groups+channels) or comma-separated IDs/usernames
- `--output` — Output file path (default: `$WORKSPACE/temp/messages-TIMESTAMP.json`)

Output to stdout:
```json
{
  "output_file": "/path/to/messages.json",
  "message_count": 42,
  "chats_scanned": 15
}
```

Updates the `state` table in tg-client.db to track last processed message per chat (avoids re-fetching on next run).

### get-comments — Comments on a channel post
```bash
tg.py get-comments --channel CHANNEL --post_id MESSAGE_ID [--limit 50]
```

Output:
```json
[
  {
    "message_id": 55555,
    "sender_id": 111222,
    "sender_name": "Jane Smith",
    "sender_username": "janesmith",
    "text": "Great post!",
    "date": "2026-03-20T11:00:00+00:00",
    "reply_to_id": null
  }
]
```

Returns empty array if the channel has no linked discussion group (not an error).

### get-user — User profile info
```bash
tg.py get-user --user USER_ID_OR_USERNAME
```

Output:
```json
{
  "user_id": 123456789,
  "first_name": "John",
  "last_name": "Doe",
  "username": "johndoe",
  "phone": null,
  "bio": "Software developer",
  "is_bot": false,
  "is_verified": false,
  "personal_channel_id": null,
  "mutual_contact": false
}
```

**Important:** `personal_channel_id` is the user's personal channel (if they set one). This is the ONLY way to find a user's channel via API. You CANNOT enumerate all channels a user owns or is admin of — this is a Telegram API limitation.

### send — PROTECTED: Send a message
```bash
# Preview only (no message sent):
tg.py send --chat CHAT_ID --text "Hello"

# Actually send:
tg.py send --chat CHAT_ID --text "Hello" --confirm
```

Preview output:
```json
{"action": "preview", "chat_id": "CHAT_ID", "text": "Hello"}
```

Send output:
```json
{"action": "sent", "message_id": 12345, "chat_id": "CHAT_ID"}
```

A warning is printed to stderr when `--confirm` is used. Every send is logged to the `audit_log` table.

## Error Codes
| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error (config, API, not found, etc.) |
| 2 | Flood wait — `{"error": "flood_wait", "wait_seconds": N}` on stderr. **Wait N seconds before retrying.** |
| 3 | Not authorized — session expired or never created. Needs interactive reauth. |

## Common Errors
- `{"error": "private_channel"}` — Cannot access a private channel/group you're not a member of
- `{"error": "not_found"}` — Username or ID doesn't resolve to an entity
- `{"error": "lock_timeout"}` — Another tg.py process is running. Wait and retry.
- `{"error": "config"}` — Missing environment variables

## Cron Management (run.sh)
```bash
# Install cron (every 60 min, fetch last 12 min of messages)
OPENCLAUDE_WORKSPACE_DIR=/path/to/ws ./skills/tg-client/run.sh start

# Custom interval
OPENCLAUDE_WORKSPACE_DIR=/path/to/ws ./skills/tg-client/run.sh start --interval 10 --since-minutes 12

# Stop cron
OPENCLAUDE_WORKSPACE_DIR=/path/to/ws ./skills/tg-client/run.sh stop

# Check status
OPENCLAUDE_WORKSPACE_DIR=/path/to/ws ./skills/tg-client/run.sh status
```

## Agent Usage Patterns

### BFS channel discovery
1. Start with seed channel(s) from user
2. Call `get-similar` on each seed to get candidates
3. For each candidate, call `channel-info` to check criteria (subscribers, avg_views)
4. Recurse on passing channels' similar list (maintain a visited set to avoid cycles)
5. Respect flood_wait errors — pause for `wait_seconds` and continue
6. Collect results and generate a report

### Lead monitoring
1. Call `monitor --since-minutes 12 --output /path/to/msgs.json`
2. Read the JSON file, analyze messages for hiring intent
3. Send notifications via telegram-sender for valid leads

### User/channel investigation
1. `get-user --user @username` to check personal_channel_id
2. If personal_channel_id exists, call `channel-info --channel <id>`
3. `get-similar --channel <id>` for related channels
4. `get-comments --channel X --post_id Y` to read discussion

### Reading chat history
1. `list --type group` to find available groups
2. `read --chat <id> --limit 100 --since 24` to read recent messages
3. `search --query "keyword" --chat <id>` for targeted search

## Important Notes

### Session management
- Session file is shared across all commands — file lock prevents concurrent access
- Do NOT run multiple tg.py commands in parallel; the lock will serialize them, but queuing is more efficient
- Session path: tries `tg-client-session.session` first, falls back to `lead-monitor-session.session`

### Chat ID formats
- Username: `@channelname` or `channelname`
- Numeric channel/group: `-1001234567890`
- Numeric user: `123456789`
- All formats work interchangeably in `--chat`, `--channel`, `--user` args

### Rate limits
- Telegram enforces rate limits. If you get exit code 2 (flood_wait), you MUST wait the specified seconds
- For BFS-style exploration, add 1-2 second delays between API calls
- Searching across all dialogs is especially heavy — prefer targeted searches

### Database
- SQLite at `$WORKSPACE/tg-client.db`
- `state` table: tracks last_message_id per chat for the monitor command
- `audit_log` table: logs all send actions with timestamp and data
