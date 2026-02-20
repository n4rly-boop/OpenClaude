# OpenClaude

A personal AI assistant that connects Claude Code to Telegram. Send messages on Telegram, get responses powered by the full Claude CLI with tool access (file reading, web search, code execution, and more).

Built in the spirit of [OpenClaw](https://github.com/nicholasgasior/OpenClaw) -- a persistent AI companion with memory, personality, and real tools.

## How It Works

```
You (Telegram) --> telegram-bot.py --> claude CLI --> Response --> You (Telegram)
```

1. You send a message on Telegram
2. The bot calls the Claude CLI with your message
3. Claude runs with full tool access (read files, search web, execute code, etc.)
4. The response is converted from markdown to Telegram HTML and sent back
5. Session IDs are saved so conversations persist across messages

## Project Structure

```
OpenClaude/
├── telegram-bot.py              # Main bot script
├── transcribe.py                # Voice transcription (Deepgram)
├── bin/                         # Operational scripts
│   ├── start.sh                 # Start the bot (systemd or nohup)
│   ├── stop.sh                  # Stop the bot
│   ├── restart.sh               # Restart the bot
│   ├── setup.sh                 # Interactive setup wizard
│   └── ouroboros.sh             # Watchdog — auto-restarts dead bot
├── guard/                       # Security hooks
│   ├── guard.sh                 # Blocks dangerous Bash commands
│   └── guard-write.sh           # Blocks writes to protected files
├── services/                    # Daemon configs
│   ├── systemd/                 # Linux systemd units
│   │   ├── claude-telegram-bot.service
│   │   └── ouroboros.service
│   └── launchd/                 # macOS launch agents
│       ├── com.claude.telegram-bot.plist
│       └── com.claude.daily-brief.plist
├── skills/                      # Skill scripts
│   ├── telegram-sender/         # Send messages/files via Telegram API
│   ├── heartbeat/               # Periodic check-in skill
│   └── daily-brief/             # Daily briefing skill
├── memory/                      # Memory system
│   ├── MEMORY.md                # Long-term memory
│   └── YYYY-MM-DD.md            # Daily memory files
├── CLAUDE.md                    # Claude's operating instructions
├── SOUL.md                      # Personality and values
├── IDENTITY.md                  # Agent identity (filled on first run)
├── USER.md                      # User info (filled on first run)
├── TOOLS.md                     # Available tools and environment
├── BOOTSTRAP.md                 # First-run ritual (self-deletes)
├── .claude/
│   └── settings.json            # Claude Code permissions & hooks
├── .env.example                 # Environment template
├── .gitignore
└── requirements.txt
```

## Setup

### Prerequisites

- Python 3.11+
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Your Telegram user ID (from [@userinfobot](https://t.me/userinfobot))

### Quick Setup

Run the interactive setup wizard:

```bash
git clone https://github.com/n4rly-boop/OpenClaude.git
cd OpenClaude
bash bin/setup.sh
```

This will check prerequisites, configure your `.env`, install Python dependencies, and optionally set up daemon services.

### Manual Installation

1. **Clone the repo:**
   ```bash
   git clone https://github.com/n4rly-boop/OpenClaude.git
   cd OpenClaude
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Create your `.env` file:**
   ```bash
   cp .env.example .env
   # Edit .env with your values
   ```

4. **Start the bot:**
   ```bash
   bash bin/start.sh
   ```

### First Run

On first launch, if `BOOTSTRAP.md` exists, Claude will enter bootstrap mode and guide you through:
- Choosing a name and identity for your AI
- Recording your preferences
- Reviewing the SOUL.md values together
- Creating the first memory entry

## Running as a Service

### Linux (systemd)

`bin/start.sh` automatically installs and starts the systemd service. To manage manually:

```bash
systemctl --user status claude-telegram-bot
systemctl --user restart claude-telegram-bot
journalctl --user -u claude-telegram-bot -f
```

### macOS (launchd)

Run `bin/setup.sh` and select "yes" for launchd setup, or install manually:

```bash
cp services/launchd/com.claude.telegram-bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.claude.telegram-bot.plist
```

### Ouroboros Watchdog

The ouroboros watchdog (`bin/ouroboros.sh`) monitors the bot service and auto-restarts it if it dies. It runs as its own systemd service:

```bash
systemctl --user enable --now ouroboros
```

Configure the check interval via `OUROBOROS_INTERVAL` (default: 30 seconds).

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Show welcome message |
| `/new` | Clear session, start a fresh conversation |
| `/status` | Show your user ID, session info, and bot config |

## Features

- **Session continuity** -- conversations persist across messages using Claude's `--resume` flag
- **Full tool access** -- Claude can read/write files, search the web, run shell commands, and more
- **Memory system** -- long-term memory (`memory/MEMORY.md`) and daily journals (`memory/YYYY-MM-DD.md`)
- **Voice messages** -- send voice notes or audio files; they are transcribed via Deepgram and routed to Claude
- **File and photo handling** -- send documents or photos; Claude receives the file path and can read/analyze them
- **Telegram HTML rendering** -- markdown responses are converted to Telegram-compatible HTML
- **Message splitting** -- long responses are automatically split at paragraph/sentence boundaries
- **User authorization** -- only allowed Telegram user IDs can interact with the bot
- **Telegram sender skill** -- Claude can proactively send messages and files via Telegram
- **Heartbeat & daily briefs** -- scheduled skills for periodic check-ins and morning briefings

### Voice Messages

Send a voice message or audio file on Telegram and the bot will transcribe it via [Deepgram](https://deepgram.com/), then pass the text to Claude. Requires a `DEEPGRAM_API_KEY` in your `.env`:

```env
DEEPGRAM_API_KEY=your-deepgram-key
```

### File and Photo Handling

Send a document or photo on Telegram and the bot will download it to `workspaces/uploads/YYYY-MM-DD/` and tell Claude the file path. Claude can then read, analyze, or process the file using its tools. Add a caption to your file to give Claude context about what you want done with it.

## Security & Permissions

Only users listed in `ALLOWED_USERS` can interact with the bot. The first user in the list is the **admin**.

### What everyone can do

- Read files, search the codebase, browse the web
- Run shell commands (`ls`, `curl`, `python3`, etc.)
- Install packages (`apt`, `pip`, `npm`, `cargo`, etc.)
- Use `git` and `gh` CLI (with their own credentials)
- Use `yt-dlp`, `ffmpeg`, and other installed tools
- Write to memory files

### What everyone is blocked from (enforced by guard hooks)

| Blocked action | Why |
|---|---|
| `systemctl`, `service`, `kill`, `pkill`, `killall` | Prevents killing the bot or other services |
| Modifying SSH config, `authorized_keys`, `/etc/ssh` | Prevents SSH lockout |
| `iptables`, `ufw`, `nftables` | Prevents firewall lockout |
| Bringing down network interfaces | Prevents network lockout |
| Modifying PAM / NSS config | Prevents auth lockout |
| Modifying the `root` user account | Prevents admin lockout |
| Writing to guard scripts or `.claude/settings.json` | Prevents disabling security |

### Additional non-admin restrictions

| Blocked action | Why |
|---|---|
| Reading host env vars (`env`, `printenv`, `/proc/*/environ`) | Prevents credential leaks |
| Reading credential files (`.env`, `.ssh/`, `.aws/`, `.npmrc`, etc.) | Prevents credential leaks |
| `chmod`/`chown` outside their workspace | Workspace isolation |
| `rm -rf` outside their workspace | Workspace isolation |
| Writing/editing files outside their workspace | Workspace isolation |

### Per-user environments

Each user gets an isolated workspace at `workspaces/c{chat_id}/`. Users can have their own `.env` file in their workspace to set credentials (e.g. `GH_TOKEN` for their own GitHub account). Admin inherits the full host environment; non-admin users only get safe system vars plus their workspace `.env`.

## License

MIT
