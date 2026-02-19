# Phase 1: Foundation

## Step 1.1 — Create project scaffold

Create the directory structure:
```
OpenClaude/
├── telegram-bot.py
├── requirements.txt
├── .env.example
├── .gitignore
├── CLAUDE.md
├── SOUL.md
├── IDENTITY.md
├── USER.md
├── TOOLS.md
├── BOOTSTRAP.md
├── memory/
│   └── .gitkeep
├── uploads/
│   └── .gitkeep
├── skills/
│   ├── telegram-sender/
│   │   ├── SKILL.md
│   │   └── send.sh
│   └── daily-brief/
│       └── SKILL.md
├── launchd/
│   ├── com.claude.telegram-bot.plist
│   └── com.claude.daily-brief.plist
├── systemd/
│   └── claude-telegram-bot.service
└── .claude/
    └── settings.json
```

## Step 1.2 — Create .gitignore

Ignore: .env, uploads/, memory/ contents (keep dirs), __pycache__, *.pyc, .telegram-claude-sessions.json, *.ogg, *.wav

## Step 1.3 — Create .env.example

Variables:
- TELEGRAM_BOT_TOKEN=
- ALLOWED_USERS=          (comma-separated Telegram user IDs)
- WHISPER_BACKEND=local   (local|groq)
- GROQ_API_KEY=           (if using groq)
- CLAUDE_MODEL=           (optional, override)
- WORKING_DIR=            (optional, defaults to script dir)

## Step 1.4 — Create requirements.txt

```
python-telegram-bot>=21.0
python-dotenv
```

## Step 1.5 — Create SOUL.md

Based on OpenClaw's version. Core personality:
- Be genuinely helpful, not performatively helpful
- Have opinions, disagree, find things amusing
- Be resourceful before asking
- Earn trust through competence
- Remember you're a guest in someone's life
- Boundaries: private stays private, ask before external actions
- Vibe: concise when needed, thorough when it matters

## Step 1.6 — Create IDENTITY.md

Template for the agent to fill in during first conversation:
- Name, Creature, Vibe, Emoji, Avatar

## Step 1.7 — Create USER.md

Template for user info:
- Name, timezone, how to address, preferences, notes

## Step 1.8 — Create TOOLS.md

Template for environment-specific config:
- SSH hosts, API keys notes, local services, etc.

## Step 1.9 — Create BOOTSTRAP.md

First-run ritual:
- Agent introduces itself, asks who it is / who you are
- Fills in IDENTITY.md and USER.md together
- Reviews SOUL.md together
- Optionally sets up Telegram
- Self-deletes after bootstrap complete

## Step 1.10 — Create CLAUDE.md

Merged workspace instructions:
- Read SOUL.md, IDENTITY.md, USER.md on startup
- Memory system: memory/MEMORY.md (long-term) + memory/YYYY-MM-DD.md (daily)
- Telegram constraints: 4096 char limit, HTML formatting
- Group chat rules: react like a human, don't dominate
- Heartbeat: periodic check-ins, batch tasks
- Safety: read freely, ask before external actions
- Tools available: Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch, Task, Skill

## Step 1.11 — Create telegram-bot.py

Core bot with:
- python-telegram-bot v21+ (async)
- Load .env via python-dotenv
- ALLOWED_USERS enforcement
- Session persistence: ~/.openclaude-sessions.json
- Message handler: text → `claude -p "message" --resume SESSION --output-format json --allowedTools ...`
- Parse JSON response, extract text
- TelegramRenderer: markdown → Telegram HTML (headings, code blocks, lists, bold, italic)
- 4096 char split for long messages
- /new command: clear session
- /status command: show user ID + session info
- Error handling: catch claude CLI errors, report to user
- Logging

## Step 1.12 — Create skills/telegram-sender/

SKILL.md + send.sh:
- send.sh accepts --text "message" or --file /path
- Uses Telegram Bot API via curl
- Reads BOT_TOKEN and CHAT_ID from env or args

## Step 1.13 — Create launchd/ and systemd/ templates

Daemon templates for continuous operation.

## Step 1.14 — Create .claude/settings.json

Basic hooks for memory injection on session start.

## Step 1.15 — Create README.md

Project documentation, setup guide, architecture diagram.
