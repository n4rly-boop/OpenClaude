# Phase 2: Full Telegram Media Support

## Goal
Extend telegram-bot.py to handle voice messages, inbound files/photos/documents, and outbound files.

## Step 2.1 — Voice Message Handler

Add handler for `filters.VOICE | filters.AUDIO`:
1. Download .ogg file from Telegram to `uploads/voice/`
2. Transcribe using configurable backend:
   - `local`: use `openai-whisper` or `faster-whisper` (pip package)
   - `groq`: use Groq Whisper API (requires GROQ_API_KEY)
3. Build message: `[Voice message transcription]: "{text}"`
4. Route to Claude via existing `call_claude()`

```python
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice = update.message.voice or update.message.audio
    file = await context.bot.get_file(voice.file_id)
    ogg_path = UPLOADS_DIR / "voice" / f"{voice.file_id}.ogg"
    await file.download_to_drive(ogg_path)
    text = await transcribe(ogg_path)
    response = await call_claude(f'[Voice message]: "{text}"', user_id)
    await send_rendered(update, response, context)
```

Config: `WHISPER_BACKEND` env var (already in .env.example)

## Step 2.2 — Transcription Backends

Create `transcribe.py` module with:
```python
async def transcribe(audio_path: Path) -> str:
    backend = os.getenv("WHISPER_BACKEND", "local")
    if backend == "groq":
        return await transcribe_groq(audio_path)
    return await transcribe_local(audio_path)

async def transcribe_local(audio_path: Path) -> str:
    # Use faster-whisper (smaller, faster than openai-whisper)
    # Run in thread pool to avoid blocking async loop
    ...

async def transcribe_groq(audio_path: Path) -> str:
    # POST to Groq Whisper API
    ...
```

## Step 2.3 — Inbound Files / Documents / Photos

Add handler for `filters.Document.ALL | filters.PHOTO`:
1. Download file to `uploads/YYYY-MM-DD/`
2. For photos: download largest size, save as .jpg
3. For documents: save with original filename
4. Build message telling Claude where the file is:
   - `[File received: uploads/2026-02-19/report.pdf] User caption: "check this"`
   - `[Photo received: uploads/2026-02-19/photo_123.jpg] User caption: "what's in this?"`
5. Route to Claude — Claude can then Read the file

```python
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    today = datetime.now().strftime("%Y-%m-%d")
    dest_dir = UPLOADS_DIR / today
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / doc.file_name
    file = await context.bot.get_file(doc.file_id)
    await file.download_to_drive(dest)
    caption = update.message.caption or ""
    msg = f'[File received: {dest.relative_to(SCRIPT_DIR)}]'
    if caption:
        msg += f' User says: "{caption}"'
    response = await call_claude(msg, user.id)
    await send_rendered(update, response, context)
```

## Step 2.4 — Outbound File Support (telegram-sender enhancement)

The send.sh script already supports `--file` flag. No changes needed for Phase 2.
Claude can already use it via Bash tool:
```bash
bash skills/telegram-sender/send.sh --file /path/to/file --chat CHAT_ID
```

## Step 2.5 — Update requirements.txt

Add:
```
faster-whisper>=1.0.0
groq>=0.4.0
```

## Step 2.6 — Update README.md

Add voice and file handling sections to Features and Setup.

## Files to Change
- `telegram-bot.py` — add voice, document, photo handlers
- `transcribe.py` — new file, transcription backends
- `requirements.txt` — add faster-whisper, groq
- `README.md` — document new features
