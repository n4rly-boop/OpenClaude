# Skill: daily-brief

## Purpose
Generate and deliver a daily briefing to the user via Telegram.
This skill is designed to be run on a schedule (e.g., every morning via cron/launchd/systemd).

## What It Does
When invoked, Claude will:
1. Read memory files to understand current context
2. Check for any pending tasks or reminders
3. Summarize what happened yesterday (from daily memory)
4. Note any upcoming deadlines or events
5. Deliver a concise briefing via the telegram-sender skill

## Setup
_To be implemented in Phase 2. This is a placeholder for the skill structure._

## Planned Usage
```bash
claude -p "Generate and send a daily brief" --allowedTools Read,Write,Bash,Glob,Grep,Skill
```

## Schedule
- Default: 9:00 AM in user's timezone
- Configurable via launchd plist or systemd timer
