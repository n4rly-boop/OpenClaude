# Tools & Environment

> This file documents the tools, services, and environment available to you.
> Update it as new tools are added or configurations change.

## Claude Code Tools

You have access to these tools when invoked via the Telegram bot:

| Tool | Purpose |
|------|---------|
| `Read` | Read files from the filesystem |
| `Write` | Write files to the filesystem |
| `Edit` | Edit existing files with find-and-replace |
| `Bash` | Execute shell commands |
| `Glob` | Find files by pattern |
| `Grep` | Search file contents with regex |
| `WebFetch` | Fetch and analyze web pages |
| `WebSearch` | Search the web |
| `Task` | Run a sub-agent for complex tasks |
| `Skill` | Execute predefined skill scripts |

## Skills

### create-skill (template) — READ FIRST
- **Location:** `skills/create-skill/SKILL.md`
- **Purpose:** Template and safety guidelines for creating new skills
- **Usage:** Read `skills/create-skill/SKILL.md` before creating any new skill. Follow all safety rules.
- **Key rules:**
  - User-facing skills source **only `$PWD/.env`** (workspace), never project root `.env`
  - Never hardcode credentials, never exfiltrate user data
  - Never modify system services, guard scripts, or security hooks
  - Validate all inputs, prevent path traversal, use timeouts

### telegram-sender
- **Location:** `skills/telegram-sender/send.sh`
- **Purpose:** Send messages and files to Telegram chats directly
- **Usage:** `send.sh --text "message" --chat CHAT_ID` or `send.sh --file /path/to/file --chat CHAT_ID`

### ssh-vps
- **Location:** `skills/ssh-vps/run.sh`
- **Purpose:** Run commands on the VPS over SSH via sshpass
- **Usage:** `./skills/ssh-vps/run.sh "command"`
- **Examples:**
  ```bash
  ./skills/ssh-vps/run.sh "df -h"
  ./skills/ssh-vps/run.sh "uptime && free -h"
  ./skills/ssh-vps/run.sh "cat /var/log/syslog | tail -50"
  ```
- **Credentials:** Read from the user's workspace `.env` file (`VPS_HOST`, `VPS_PORT`, `VPS_USER`, `VPS_PASSWORD`). Never hardcode credentials.

### moodle
- **Location:** `skills/moodle/run.sh`
- **Purpose:** Log into Innopolis University Moodle via SSO and fetch upcoming deadlines
- **Usage:** `./skills/moodle/run.sh [deadlines|courses]`
- **Commands:**
  - `deadlines` (default) — upcoming assignment deadlines (next 90 days)
  - `courses` — list current semester [S26] courses
  - `lectures [course_id]` — list resources for a course (default: NLP id=3440)
  - `download <resource_id> [outdir]` — download a file; prints saved path to stdout
- **Credentials:** Read from workspace `.env` (`MOODLE_USERNAME`, `MOODLE_PASSWORD`)

### kaggle-compete
- **Location:** `skills/kaggle-compete/run.sh`
- **Purpose:** Automatically solve Kaggle competitions by creating a solution notebook
- **Usage:** `./skills/kaggle-compete/run.sh <competition_url>`
- **Example:** `./skills/kaggle-compete/run.sh "https://www.kaggle.com/t/ABC123..."`
- **Required:** `KAGGLE_USERNAME`, `KAGGLE_KEY` in workspace `.env`
- **Output:** Creates private notebook, outputs URL to stdout and `temp/notebook_url.txt`
- **Note:** User must accept competition rules via browser first (Kaggle limitation)

### daily-brief (planned)
- **Location:** `skills/daily-brief/`
- **Purpose:** Generate and deliver daily briefings

## Environment

### Server
- **OS:** Ubuntu 22.04 LTS (Linux 5.15.0)
- **Working Directory:** defaults to `/root/OpenClaude`

### SSH Hosts
- Configured per-workspace via `.env` files in `workspaces/c{chat_id}/.env`
- Required variables: `VPS_HOST`, `VPS_PORT`, `VPS_USER`, `VPS_PASSWORD`

### API Keys & Services
- **Anthropic Claude API** — `ANTHROPIC_API_KEY` in project `.env`
- **Telegram Bot API** — `TELEGRAM_BOT_TOKEN` in project `.env`
- **Deepgram** — `DEEPGRAM_API_KEY` in project `.env` (voice transcription)
- Per-workspace keys: stored in `workspaces/c{chat_id}/.env`

### Local Services

#### pinchtab (headless browser)
- **Service:** systemd `pinchtab.service` (auto-starts on boot)
- **HTTP API:** `localhost:3000`
- **Chrome CDP:** `localhost:9222`
- **Usage:** `pinchtab "<url>"` (CLI) or `pinchtab-fetch "<url>"` (returns page text)
- **API endpoints:** `POST /navigate` (param: `newTab: bool`, returns `tabId`), `GET /text`, `GET /snapshot`, `GET /screenshot?tabId=X` (returns `{"base64": "..."}` JSON), `POST /click`, `POST /type`
- **Tab cleanup:** `GET localhost:{cdpPort}/json/close/{tabId}` via Chrome CDP
- **⚠️ ALWAYS close tabs when done.** Every `newTab: true` must be paired with a close or the renderer process leaks RAM\/CPU indefinitely. Pattern:
  ```bash
  CDP_PORT=$(ss -tlnp | grep -oP "127\.0\.0\.1:\\K\\d+" | while read p; do curl -s --max-time 1 http://localhost:$p/json/version 2>/dev/null | grep -q Chrome && echo $p && break; done)
  curl http://localhost:$CDP_PORT/json/close/{tabId}
  ```

## Sending Files to the User

To deliver a file you created to the user in Telegram, write a line in your response using the 📎 marker:

```
📎 /absolute/path/to/file optional caption here
```

The bot will:
1. Strip the 📎 line from the displayed message
2. Send the file using the appropriate Telegram media type based on extension:
   - **Photos:** `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp`
   - **Videos:** `.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`
   - **Audio:** `.mp3`, `.ogg`, `.wav`, `.flac`, `.aac`, `.m4a`, `.opus`
   - **Documents:** everything else (`.pdf`, `.zip`, `.py`, `.txt`, etc.)
3. Include the caption (if provided) with the file

**Rules:**
- The path must be **absolute** and inside the current workspace directory (security enforced)
- The file must exist on disk at the time the response is sent
- You can include multiple 📎 lines in a single response
- Each 📎 must be on its own line

**Example:**
```
Here's the chart you requested:
📎 /root/OpenClaude/workspaces/c12345/output/chart.png Sales data visualization
```

## Notes
_Add environment-specific notes here as you discover them_
