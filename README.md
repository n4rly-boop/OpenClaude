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
├── telegram-bot.py          # Main bot script
├── CLAUDE.md                # Claude's operating instructions
├── SOUL.md                  # Personality and values
├── IDENTITY.md              # Agent identity (filled on first run)
├── USER.md                  # User info (filled on first run)
├── TOOLS.md                 # Available tools and environment
├── BOOTSTRAP.md             # First-run ritual (self-deletes)
├── memory/                  # Memory system
│   ├── MEMORY.md            # Long-term memory
│   └── YYYY-MM-DD.md        # Daily memory files
├── skills/
│   ├── telegram-sender/     # Send messages/files via Telegram API
│   │   ├── SKILL.md
│   │   └── send.sh
│   └── daily-brief/         # Daily briefing skill (planned)
│       └── SKILL.md
├── launchd/                 # macOS daemon configs
├── systemd/                 # Linux daemon configs
├── .claude/
│   └── settings.json        # Claude Code permissions
├── .env.example             # Environment template
├── .gitignore
└── requirements.txt
```

## Setup

### Prerequisites

- Python 3.11+
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Your Telegram user ID (from [@userinfobot](https://t.me/userinfobot))

### Installation

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
   ```

4. **Edit `.env` with your values:**
   ```env
   TELEGRAM_BOT_TOKEN=your-bot-token-here
   ALLOWED_USERS=your-telegram-user-id
   ```

5. **Verify Claude CLI works:**
   ```bash
   claude -p "Hello" --output-format json
   ```

6. **Make the telegram-sender skill executable:**
   ```bash
   chmod +x skills/telegram-sender/send.sh
   ```

7. **Start the bot:**
   ```bash
   python telegram-bot.py
   ```

### First Run

On first launch, if `BOOTSTRAP.md` exists, Claude will enter bootstrap mode and guide you through:
- Choosing a name and identity for your AI
- Recording your preferences
- Reviewing the SOUL.md values together
- Creating the first memory entry

### Running as a Service

#### Linux (systemd)

Edit the paths in `systemd/claude-telegram-bot.service`, then:

```bash
sudo cp systemd/claude-telegram-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable claude-telegram-bot
sudo systemctl start claude-telegram-bot
```

#### macOS (launchd)

Edit the paths in `launchd/com.claude.telegram-bot.plist`, then:

```bash
cp launchd/com.claude.telegram-bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.claude.telegram-bot.plist
```

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
- **Voice messages** -- send voice notes or audio files; they are transcribed and routed to Claude
- **File and photo handling** -- send documents or photos; Claude receives the file path and can read/analyze them
- **Telegram HTML rendering** -- markdown responses are converted to Telegram-compatible HTML
- **Message splitting** -- long responses are automatically split at paragraph/sentence boundaries
- **User authorization** -- only allowed Telegram user IDs can interact with the bot
- **Telegram sender skill** -- Claude can proactively send messages and files via Telegram

### Voice Messages

Send a voice message or audio file on Telegram and the bot will transcribe it, then pass the text to Claude. Two transcription backends are supported:

- **local** (default) -- uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) to transcribe on your machine. No API key needed, but requires the `faster-whisper` Python package and enough CPU/RAM for the model. Set the model size with `WHISPER_MODEL` (default: `base`).
- **groq** -- uses [Groq's Whisper API](https://console.groq.com/) for fast cloud transcription. Requires a `GROQ_API_KEY` in your `.env`.

Set the backend via the `WHISPER_BACKEND` environment variable in `.env`:
```env
WHISPER_BACKEND=local   # or "groq"
WHISPER_MODEL=base      # for local backend: tiny, base, small, medium, large-v3
GROQ_API_KEY=gsk_...    # required for groq backend
```

### File and Photo Handling

Send a document or photo on Telegram and the bot will download it to `uploads/YYYY-MM-DD/` and tell Claude the file path. Claude can then read, analyze, or process the file using its tools. Add a caption to your file to give Claude context about what you want done with it.

## Security

- Only users listed in `ALLOWED_USERS` can interact with the bot
- The `.env` file (containing tokens) is gitignored
- Claude follows safety rules defined in `CLAUDE.md` (ask before external actions)
- Session data is stored locally at `~/.openclaude-sessions.json`

## License

MIT
