# Phase 5: Daemon + Skills + Heartbeat

## Goal
Create the heartbeat skill, refine daemon templates, add setup script.

## Step 5.1 — Create skills/heartbeat/

SKILL.md + heartbeat.sh:
- Invoked on schedule (cron/launchd)
- Runs `claude -p "Run heartbeat check. Review pending tasks, check memory for reminders, send brief update via telegram-sender if anything notable."`
- Reads heartbeat-state.json for last run time
- Updates heartbeat-state.json after run

## Step 5.2 — Flesh out skills/daily-brief/

Update SKILL.md with actual implementation:
- Morning briefing that reads memory, calendar, weather, and sends summary via telegram-sender

## Step 5.3 — Create setup.sh

Interactive setup script:
- Check prerequisites (python3, claude CLI, pip)
- Create .env from .env.example with prompts
- Install pip dependencies
- Make send.sh executable
- Detect OS and set up daemon (systemd or launchd)
- Replace /path/to/OpenClaude placeholders

## Step 5.4 — Add cron templates for Linux

Alternative to systemd timers for heartbeat/daily-brief scheduling.

## Files to Create/Change
- `skills/heartbeat/SKILL.md` — heartbeat skill docs
- `skills/heartbeat/run.sh` — heartbeat runner script
- `skills/daily-brief/SKILL.md` — update with implementation
- `skills/daily-brief/run.sh` — daily brief runner
- `setup.sh` — interactive setup script
- `heartbeat-state.json` — template (gitignored)
