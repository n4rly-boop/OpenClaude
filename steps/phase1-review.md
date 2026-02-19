# Phase 1 Review

Reviewer: Claude Opus 4.6
Date: 2026-02-19

---

## 1. telegram-bot.py

### CRITICAL Issues

**C1. Typing indicator stops after 5 seconds; Claude CLI calls can take 30-120s**
- Severity: **Critical (UX)**
- The bot sends `ChatAction.TYPING` once at line 458, but Telegram's typing indicator expires after ~5 seconds. For long Claude calls, the user sees no feedback and may think the bot is dead.
- Fix: Run a background task that re-sends the typing indicator every 4-5 seconds until Claude responds.

```python
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_authorized(user.id):
        return

    message_text = update.message.text
    if not message_text:
        return

    # Continuous typing indicator
    stop_typing = asyncio.Event()

    async def keep_typing():
        while not stop_typing.is_set():
            try:
                await update.message.chat.send_action(ChatAction.TYPING)
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_typing.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                pass

    typing_task = asyncio.create_task(keep_typing())

    try:
        response = await call_claude(message_text, user.id)
        stop_typing.set()
        await typing_task
        await send_rendered(update, response, context)
    except Exception:
        stop_typing.set()
        await typing_task
        raise
```

**C2. No timeout on Claude CLI subprocess**
- Severity: **Critical**
- `proc.communicate()` at line 282 has no timeout. If the Claude CLI hangs (waiting for input, network stall, infinite tool loop), the bot blocks forever for that user, and the asyncio event loop task never completes.
- Fix: Add a timeout.

```python
try:
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
except asyncio.TimeoutError:
    proc.kill()
    await proc.communicate()
    logger.error("Claude CLI timed out after 300s for user %d", user_id)
    return "Claude took too long to respond. Try again or /new to start fresh."
```

**C3. Race condition in session file read/write**
- Severity: **Critical**
- `load_sessions()` and `save_sessions()` read and write the same JSON file without any locking. If two messages arrive near-simultaneously (e.g., two allowed users, or a user double-sends), both calls read the old state, and the second write clobbers the first, losing a session ID.
- Fix: Use `fcntl.flock()` or `filelock` library for file-level locking, or switch to an atomic write pattern (write to temp file, then `os.replace()`). At minimum, do atomic writes:

```python
import tempfile

def save_sessions(sessions: dict) -> None:
    try:
        fd, tmp_path = tempfile.mkstemp(dir=SESSION_FILE.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(sessions, f, indent=2)
        os.replace(tmp_path, SESSION_FILE)
    except OSError as e:
        logger.error("Failed to save sessions: %s", e)
```

### MEDIUM Issues

**M1. HTML fallback in `send_rendered` sends ALL chunks as plain text even if only one failed**
- Severity: **Medium**
- At line 374-379, when HTML parsing fails on one chunk, the code falls back to sending the entire original `text` as plain (not just the remaining chunks). If the first chunk already sent successfully as HTML, the user sees that chunk twice: once as HTML, once as plain.
- Fix: Track which chunks have been sent, and only fall back for the failed chunk onward. Or better, fall back per-chunk:

```python
for chunk in chunks:
    try:
        await update.message.reply_text(
            chunk, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        )
    except Exception:
        logger.warning("HTML parse failed for chunk, falling back to plain text")
        # Strip HTML tags and send as plain
        import re as _re
        plain = _re.sub(r"<[^>]+>", "", chunk)
        await update.message.reply_text(plain)
```

**M2. Markdown-to-HTML renderer has edge cases**
- Severity: **Medium**
- The `\[([^\]]+)\]\(([^)]+)\)` link regex at line 177 runs AFTER `html.escape()`, so `&amp;` and other entities in URLs will be double-escaped inside the `href`. Markdown link syntax `[text](url)` after html.escape becomes `[text](url)` only if no special chars exist, but URLs often contain `&` which becomes `&amp;` after escaping, then gets placed inside `href="..."` which is already HTML context. Telegram may reject the URL.
- Fix: Extract links before `html.escape()` like code blocks, or unescape URLs in the href.

**M3. Code block regex doesn't handle code blocks without trailing newline after opening fence**
- Severity: **Medium**
- The regex `r"```(\w*)\n(.*?)```"` requires a `\n` after the language tag. A code block like ` ```python print("hi")``` ` (all on one line) won't match. Also, ` ``` ` (with no language but no newline) won't match.
- Fix: Make the newline optional: `r"```(\w*)\n?(.*?)```"`

**M4. `--allowedTools` flag may not exist in all Claude CLI versions**
- Severity: **Medium**
- The `--allowedTools` flag at line 261 is passed to the Claude CLI. If the user has a different version of Claude CLI, this may not be recognized or may have a different flag name. The README says to verify with `claude -p "Hello" --output-format json` but doesn't test `--allowedTools`.
- Fix: Document the required Claude CLI version in README.md. Add a startup check that verifies the CLI supports needed flags.

**M5. User message is passed as a CLI argument, not via stdin**
- Severity: **Medium (Security)**
- At line 258, the user's message is passed as `-p message`, which means it appears in `ps aux` output and process lists. Long messages could also hit OS argument length limits (typically ~128KB-2MB, but still a concern). More importantly, the message is visible to any user on the system who can run `ps`.
- Fix: Pass the message via stdin instead of as an argument. Check if the Claude CLI supports stdin input (e.g., `echo "msg" | claude -p - --output-format json`). If not, use a temp file.

**M6. No concurrent request protection per user**
- Severity: **Medium**
- If a user sends multiple messages rapidly, multiple Claude CLI processes will spawn concurrently, all potentially trying to resume the same session. This could cause session conflicts or race conditions in Claude's session system.
- Fix: Add a per-user lock (e.g., `asyncio.Lock()` per user_id in a dict) so messages from the same user are processed sequentially.

```python
user_locks: dict[int, asyncio.Lock] = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_authorized(user.id):
        return
    if user.id not in user_locks:
        user_locks[user.id] = asyncio.Lock()
    async with user_locks[user.id]:
        # ... existing logic ...
```

**M7. `.gitignore` entry `~/.openclaude-sessions.json` is not a valid git pattern**
- Severity: **Medium**
- Line 16 of `.gitignore` has `~/.openclaude-sessions.json`. Git doesn't expand `~`. This pattern does nothing. However, the session file is in the home directory, not the repo, so git wouldn't track it anyway. The `.telegram-claude-sessions.json` entry on line 15 references an old/unused filename.
- Fix: Remove both entries (they're not in the repo directory), or replace with a comment explaining the session file lives in `$HOME`.

### LOW Issues

**L1. `ALLOWED_USERS` parsing silently ignores negative user IDs**
- Severity: **Low**
- `uid.isdigit()` at line 50 returns False for negative numbers. Telegram user IDs are always positive, but group chat IDs are negative. If someone mistakenly puts a group chat ID in `ALLOWED_USERS`, it's silently ignored with no warning.
- Fix: Add a log warning for non-numeric or negative values:

```python
for uid in ALLOWED_USERS_RAW.split(","):
    uid = uid.strip()
    if not uid:
        continue
    try:
        parsed = int(uid)
        if parsed <= 0:
            logger.warning("Ignoring non-positive user ID: %s", uid)
        else:
            ALLOWED_USERS.add(parsed)
    except ValueError:
        logger.warning("Ignoring non-numeric ALLOWED_USERS entry: %s", uid)
```

**L2. Logging includes message content (first 100 chars)**
- Severity: **Low (Privacy)**
- Line 463-465 logs the first 100 chars of every message. While useful for debugging, this could capture sensitive user content in log files. The SOUL.md says "Private stays private."
- Fix: In production, consider logging only message length, or making this debug-level.

**L3. `split_message` doesn't account for HTML tag splitting**
- Severity: **Low**
- The message splitter at line 201 operates on rendered HTML but doesn't ensure it doesn't split in the middle of an HTML tag. For example, splitting inside `<a href="...` would produce invalid HTML.
- Fix: This is an edge case because the 4096-byte limit is rarely hit with well-structured HTML, but a more robust splitter would track open tags. Low priority since Telegram will reject invalid HTML and the fallback sends plain text.

**L4. No graceful shutdown handling**
- Severity: **Low**
- The bot uses `app.run_polling()` which handles SIGINT, but there's no cleanup of in-flight Claude CLI processes. If the bot is stopped while a Claude call is running, the subprocess is orphaned.
- Fix: Track running subprocesses and kill them on shutdown.

**L5. `cmd_status` doesn't HTML-escape the username**
- Severity: **Low**
- Line 429: `@{user.username or 'N/A'}` is inserted into an HTML message without escaping. A crafted Telegram username with `<` characters could break the HTML. Unlikely in practice since Telegram sanitizes usernames.
- Fix: `@{html.escape(user.username) if user.username else 'N/A'}`

---

## 2. Prompt Files (SOUL.md, IDENTITY.md, USER.md, CLAUDE.md, TOOLS.md, BOOTSTRAP.md)

### Quality Assessment: Strong

The prompt files are well-crafted and internally consistent. They match the OpenClaw philosophy well.

### Issues

**M8. CLAUDE.md tells Claude to use HTML formatting, but Claude generates Markdown**
- Severity: **Medium (Contradiction)**
- CLAUDE.md line 53 says "Use HTML formatting (the bot converts your markdown to Telegram HTML)". This is self-contradictory. If the bot converts markdown to HTML, Claude should write markdown, not HTML. If Claude writes HTML AND the bot also converts markdown to HTML, the HTML tags will get double-escaped.
- Fix: Clarify that Claude should write standard Markdown and the bot handles the conversion:

```markdown
### Formatting
- Write standard **Markdown** — the bot automatically converts it to Telegram HTML
- Code blocks, bold, italic, links, and lists are all supported
- Don't write raw HTML — the converter handles that
```

**M9. CLAUDE.md startup sequence asks Claude to read files, but the bot doesn't prompt Claude to do this**
- Severity: **Medium**
- CLAUDE.md says "Every time you start a new session: Read SOUL.md, Read IDENTITY.md..." but the bot just passes the user's message directly to `claude -p "message"`. There's no system prompt or prepended instruction telling Claude to read these files first.
- Fix: Either prepend a system prompt or a preamble to the first message in a new session:

```python
if not session_id:
    # First message in session — tell Claude to bootstrap
    preamble = (
        "You are starting a new session. Read CLAUDE.md first, "
        "then follow its startup sequence before responding. "
        "The user's message is:\n\n"
    )
    message = preamble + message
```

Or better, use `--system-prompt` if the Claude CLI supports it.

**L6. BOOTSTRAP.md "Self-Destruct" step asks Claude to delete the file**
- Severity: **Low**
- This works, but means if bootstrap fails midway, the file might get deleted anyway, or it might never get deleted if the session ends prematurely. Consider having the bot check if IDENTITY.md has been filled in instead.

**L7. TOOLS.md duplicates information from CLAUDE.md**
- Severity: **Low**
- Both files contain the same tools table. When tools change, both files need updating. Consider having TOOLS.md be the single source of truth and having CLAUDE.md reference it.

---

## 3. Skills (telegram-sender/send.sh)

### Issues

**M10. `source "$PROJECT_DIR/.env"` can execute arbitrary code**
- Severity: **Medium (Security)**
- Line 14 of `send.sh` sources the `.env` file. If `.env` contains shell commands (even accidentally), they will be executed. Using `set -a` / `source` / `set +a` is a common pattern, but it's not safe if the `.env` file isn't trusted.
- Fix: Use a safer env loading approach, or document that `.env` must contain only `KEY=VALUE` pairs:

```bash
# Safer: parse only KEY=VALUE lines
if [[ -f "$PROJECT_DIR/.env" ]]; then
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] && export "$key=$value"
    done < <(grep -E '^[A-Z_][A-Z0-9_]*=' "$PROJECT_DIR/.env")
fi
```

**M11. No input validation on TEXT content passed to curl**
- Severity: **Medium**
- The `TEXT` variable is passed directly to curl via `-F "text=$TEXT"`. While curl's `-F` handles encoding, if `TEXT` starts with `@` or `<`, curl will interpret it as a file reference. For example, `--text "@/etc/passwd"` would send the contents of `/etc/passwd` as the message text.
- Fix: Ensure TEXT values don't start with `@` or `<`:

```bash
# Sanitize text to prevent curl file interpretation
if [[ "$TEXT" == @* ]] || [[ "$TEXT" == \<* ]]; then
    TEXT=" $TEXT"
fi
```

Or better, use `--data-urlencode` or the curl `--form` with explicit string prefix.

**L8. No `--silent` on curl progress output**
- Severity: **Low**
- Already uses `-s`, so this is fine. No issue.

---

## 4. Config Files

### .env.example

**L9. Missing `TELEGRAM_CHAT_ID` variable**
- Severity: **Low**
- The `send.sh` script uses `TELEGRAM_CHAT_ID` as a default, but `.env.example` doesn't include it. Users of the telegram-sender skill won't know to set it.
- Fix: Add to `.env.example`:

```
# Default Telegram chat ID for proactive messages (from telegram-sender skill)
TELEGRAM_CHAT_ID=
```

### .gitignore

**L10. Committed `__pycache__` directory**
- Severity: **Low**
- There's a `__pycache__/telegram-bot.cpython-310.pyc` file already in the repo. The `.gitignore` ignores `__pycache__/` but the file was likely committed before the gitignore was added.
- Fix: `git rm -r --cached __pycache__/`

### requirements.txt

**L11. No version pinning for python-dotenv**
- Severity: **Low**
- `python-dotenv` has no version constraint. For reproducibility, consider pinning: `python-dotenv>=1.0`

### .claude/settings.json

**L12. `working_directory: "."` is relative**
- Severity: **Low**
- This is fine since Claude Code resolves it relative to the project root, but it's worth noting.

---

## 5. Daemon Files

### systemd/claude-telegram-bot.service

**M12. `User=%i` uses instance specifier, but the unit isn't a template**
- Severity: **Medium**
- Line 8: `User=%i` uses systemd's instance specifier (`%i` = instance name between `@` and `.service`). But the unit file is named `claude-telegram-bot.service`, not `claude-telegram-bot@.service`. This means `%i` resolves to an empty string, and the service runs as root.
- Fix: Either:
  - Rename to `claude-telegram-bot@.service` and document `systemctl enable claude-telegram-bot@username.service`, or
  - Replace `User=%i` with a hardcoded user or a comment telling users to fill it in:

```ini
# Change this to your username
User=your-username-here
```

**M13. `ProtectSystem=strict` may block Claude CLI operations**
- Severity: **Medium**
- `ProtectSystem=strict` makes the entire filesystem read-only except for paths listed in `ReadWritePaths`. The Claude CLI may need to write to its own config/cache directories (e.g., `~/.claude/`, `/tmp/`), which aren't listed.
- Fix: Add additional paths:

```ini
ReadWritePaths=/path/to/OpenClaude
ReadWritePaths=%h/.openclaude-sessions.json
ReadWritePaths=%h/.claude
ReadWritePaths=/tmp
```

**L13. Multiple `ReadWritePaths` lines may not work on older systemd**
- Severity: **Low**
- Some older systemd versions require these to be on a single line, space-separated:

```ini
ReadWritePaths=/path/to/OpenClaude %h/.openclaude-sessions.json
```

### launchd/com.claude.telegram-bot.plist

**M14. PATH may not include Claude CLI location**
- Severity: **Medium**
- The PATH is set to `/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin`. If Claude CLI is installed via npm globally, it might be in `~/.npm-packages/bin` or another non-standard location. The bot script calls `claude` which needs to be in PATH.
- Fix: Document that users should verify `which claude` and add its directory to the PATH in the plist.

**L14. Placeholder paths `/path/to/OpenClaude` not validated**
- Severity: **Low**
- Both launchd plists and the systemd service use `/path/to/OpenClaude` as placeholder paths. This is expected for templates but there's no sed command or setup script to help users replace them.
- Fix: Consider adding a setup script or at least documenting a one-liner: `sed -i 's|/path/to/OpenClaude|/actual/path|g' systemd/*.service launchd/*.plist`

---

## 6. README.md

### Issues

**M15. README doesn't mention the system prompt / startup sequence gap**
- Severity: **Medium**
- The README doesn't explain how Claude knows to read CLAUDE.md on startup. New users will expect Claude to behave according to the prompt files, but without a mechanism to inject the system prompt (see M9), Claude won't read them.
- Fix: Either fix M9 first, then document the behavior, or document it as a known limitation.

**L15. Missing troubleshooting section**
- Severity: **Low**
- Common issues (Claude CLI not in PATH, bot token invalid, sessions getting stale) aren't covered.

**L16. No mention of Python virtual environment**
- Severity: **Low**
- The setup guide installs dependencies with bare `pip install`. Best practice is to use a venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**L17. GitHub repo URL may not exist yet**
- Severity: **Low**
- Line 61: `git clone https://github.com/n4rly-boop/OpenClaude.git` — if this repo isn't public yet, this will confuse users.

---

## Summary

| Severity | Count | IDs |
|----------|-------|-----|
| Critical | 3 | C1, C2, C3 |
| Medium | 11 | M1, M2, M3, M4, M5, M6, M8, M9, M10, M11, M12, M13, M14, M15 |
| Low | 12 | L1-L7, L9-L11, L13-L17 |

### Top 5 Priorities (fix these first)

1. **C2 — Add timeout to Claude CLI subprocess.** Without this, a hung CLI process blocks the bot permanently.
2. **C1 — Keep typing indicator alive.** Users will think the bot is broken during 30+ second Claude calls.
3. **C3 — Atomic session file writes.** Concurrent access will corrupt session data.
4. **M9 — Inject system prompt so Claude reads its prompt files.** Without this, the entire prompt engineering (SOUL.md, IDENTITY.md, etc.) is unused — Claude never sees it.
5. **M12 — Fix systemd User=%i.** The service will run as root in its current form, which is a security issue.

### Overall Assessment

The Phase 1 implementation is a solid foundation. The code is clean, well-structured, and follows good Python practices. The prompt files are high quality and philosophically consistent with the OpenClaw approach. The main gaps are operational robustness (timeouts, concurrency, typing indicator) and a critical architectural gap where Claude is never actually instructed to read its prompt files (M9). Fixing the top 5 priorities would make this production-ready for single-user use.
