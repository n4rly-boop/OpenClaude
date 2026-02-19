# Skill: daily-brief

## Purpose
Generate and deliver a daily briefing to the user via Telegram.
This skill is designed to be run on a schedule (e.g., every morning via cron/launchd/systemd).

## What It Does
When invoked, Claude will:
1. Read memory files to understand current context (`memory/MEMORY.md` and recent daily logs)
2. Check for any pending tasks or reminders
3. Summarize what happened yesterday (from yesterday's `memory/YYYY-MM-DD.md`)
4. Note any upcoming deadlines, events, or items requiring attention
5. Deliver a concise briefing via the `telegram-sender` skill

## Implementation
The skill runs via `run.sh`, which:
1. Sources `.env` for environment variables (Telegram tokens, etc.)
2. Invokes `claude -p` with a briefing prompt and allowed tools
3. Claude reads memory files, assembles the brief, and sends it via `telegram-sender`

## Usage
```bash
# Run directly
./skills/daily-brief/run.sh

# Or invoke via claude CLI manually
claude -p "Generate a morning briefing..." \
  --allowedTools Read,Write,Edit,Bash,Glob,Grep,WebFetch,WebSearch,Skill
```

## Schedule
- Default: 9:00 AM in user's timezone
- Configurable via launchd plist (`launchd/com.claude.daily-brief.plist`) or cron
- Example crontab entry:
  ```
  0 9 * * * /path/to/OpenClaude/skills/daily-brief/run.sh
  ```

## Environment Variables
- `TELEGRAM_BOT_TOKEN` — Required for sending the brief via Telegram
- `TELEGRAM_CHAT_ID` — The chat to deliver the brief to

## Output
A Telegram message containing:
- Summary of yesterday's activity
- Pending tasks and follow-ups
- Any reminders or deadlines
- Items flagged in long-term memory
