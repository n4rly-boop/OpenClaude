# Skill: heartbeat

## Purpose
Periodic proactive check-in that reviews pending tasks, checks memory for reminders or follow-ups, and sends a brief update via Telegram if anything notable is found.

Designed to run on a schedule (e.g., every 2-4 hours via cron, launchd, or systemd timer) so Claude stays aware of ongoing work without being explicitly prompted.

## What It Does
When invoked, the heartbeat skill will:
1. Read `heartbeat-state.json` to determine when the last heartbeat ran
2. Review memory files (`memory/MEMORY.md` and today's daily memory) for pending tasks, reminders, or items flagged for follow-up
3. Check if anything notable has changed or needs attention
4. If there is something worth reporting, send a brief update via the `telegram-sender` skill
5. Update `heartbeat-state.json` with the current timestamp

## State File
`heartbeat-state.json` lives in the project root and tracks:
```json
{
  "last_run": "2025-01-15T14:30:00Z",
  "last_message_sent": "2025-01-15T09:00:00Z"
}
```
This file is gitignored since it is instance-specific.

## Usage
```bash
# Run directly
./skills/heartbeat/run.sh

# Or via claude CLI
claude -p "Run heartbeat: review pending tasks, check memory for reminders..." \
  --allowedTools Read,Write,Edit,Bash,Glob,Grep,WebFetch,WebSearch,Skill
```

## Schedule
- Recommended: every 2-4 hours during waking hours
- Configure via cron, launchd plist, or systemd timer
- Example crontab entry:
  ```
  0 */3 9-22 * * /path/to/OpenClaude/skills/heartbeat/run.sh
  ```

## Guidelines
- Be brief. Don't send a message just to say "nothing to report."
- Batch small updates into a single message rather than spamming.
- Only send a Telegram message if there is something genuinely useful to share.
- The heartbeat is a background process, not a conversation.
