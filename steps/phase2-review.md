# Phase 2 Review — Voice, Files, Photos Support

## Summary
Syntax and imports are correct. Handlers registered properly. Core flow works. Issues identified below are edge cases and security concerns.

---

## Issues

### 1. **Path Traversal Risk — handle_document() [MEDIUM]**
**File:** `/root/OpenClaude/telegram-bot.py` (line 616)

**Issue:**
```python
dest = dest_dir / doc.file_name  # doc.file_name is user-controlled
```

If `doc.file_name` contains `../` or other path traversal sequences, it could write files outside `UPLOADS_DIR/YYYY-MM-DD/`.

**Example:**
- User sends document with filename: `../../sensitive.txt`
- File gets saved to: `/root/OpenClaude/sensitive.txt` (escapes target dir)

**Fix:**
Sanitize filename using `pathlib.Path.name` or validate it:
```python
from pathlib import Path
safe_filename = Path(doc.file_name).name  # Strips path components
dest = dest_dir / safe_filename
```

Or reject files with suspicious names:
```python
if ".." in doc.file_name or doc.file_name.startswith("/"):
    await update.message.reply_text("Invalid filename.")
    return
```

---

### 2. **Missing file_name Validation — handle_document() [MEDIUM]**
**File:** `/root/OpenClaude/telegram-bot.py` (line 609, 616)

**Issue:**
`doc.file_name` can be `None` in Telegram API. No null check before use.

**Example:**
- Telegram documents without explicit filenames → `file_name = None`
- Line 616: `dest = dest_dir / None` → TypeError

**Fix:**
```python
filename = doc.file_name or f"file_{doc.file_id}"
dest = dest_dir / filename
```

---

### 3. **Missing File Existence Check — transcribe.py [LOW]**
**File:** `/root/OpenClaude/transcribe.py`

**Issue:**
No validation that audio file exists before passing to Whisper. If download fails silently, transcribe() may receive a missing path.

**Current behavior:**
- `transcribe_local()` passes `audio_path` directly to WhisperModel
- `transcribe_groq()` opens file with `open(audio_path, "rb")` → Will raise FileNotFoundError, caught silently as "[Transcription failed]"

**Risk:**
Harder to debug failed transcriptions (no distinction between missing file vs. API error).

**Fix:**
Add check in `transcribe()`:
```python
async def transcribe(audio_path: Path) -> str:
    if not audio_path.exists():
        logger.error("Audio file not found: %s", audio_path)
        return "[Transcription failed: audio file not found]"
    backend = os.getenv("WHISPER_BACKEND", "local").lower().strip()
    ...
```

---

### 4. **Unnecessary raise in transcribe_local() [LOW]**
**File:** `/root/OpenClaude/transcribe.py` (line 48)

**Issue:**
```python
except Exception as e:
    logger.exception("faster-whisper transcription error")
    raise e  # Caught immediately by outer try/except
```

The exception is re-raised but caught in outer try/except (line 55), producing identical behavior to not re-raising. Redundant re-raise.

**Fix:**
Remove the raise:
```python
except Exception as e:
    logger.exception("faster-whisper transcription error")
    # Let outer exception handler deal with it
```

Or simplify the whole function structure to avoid nested try/except.

---

### 5. **relative_to() Can Fail if Paths Diverge [LOW]**
**File:** `/root/OpenClaude/telegram-bot.py` (lines 622, 659)

**Issue:**
If UPLOADS_DIR is ever configured outside SCRIPT_DIR, `dest.relative_to(SCRIPT_DIR)` will raise ValueError:
```python
dest.relative_to(SCRIPT_DIR)  # ValueError if dest not under SCRIPT_DIR
```

**Current setup is safe** (UPLOADS_DIR = SCRIPT_DIR / "uploads"), but configuration could break this.

**Fix:**
Add safety check:
```python
try:
    relative_path = dest.relative_to(SCRIPT_DIR)
except ValueError:
    relative_path = dest  # Fall back to absolute path
```

---

### 6. **No File Size Limits — handle_document(), handle_photo() [LOW]**
**File:** `/root/OpenClaude/telegram-bot.py` (lines 610, 619, 656)

**Issue:**
No check on `doc.file_size` before download. Could download arbitrarily large files, consuming disk/memory.

Telegram has a max file size (~2GB for regular users), but no local limits enforced by bot.

**Fix:**
```python
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
if doc.file_size > MAX_FILE_SIZE:
    await update.message.reply_text(f"File too large ({doc.file_size / (1024*1024):.1f} MB > 100 MB)")
    return
```

---

## Checks Passed ✓

- **Syntax:** Both `.py` files compile without errors
- **Imports:** `transcribe` imported correctly in telegram-bot.py
- **Handler Registration:** All 4 new handlers (voice, document, photo) registered in main()
- **Voice Flow:** Correct sequence: download → transcribe → message build → Claude call
- **Typing Indicator:** Properly implemented with per-user locks to prevent concurrent calls
- **Error Handling:** All exception paths logged
- **Requirements:** faster-whisper and groq added correctly

---

## Summary of Fixes

| Severity | Issue | Fix Effort |
|----------|-------|-----------|
| MEDIUM | Path traversal in filenames | Use `Path.name` sanitization |
| MEDIUM | Missing file_name null check | Default to `file_{file_id}` |
| LOW | Missing audio file check | Pre-validate path exists |
| LOW | Redundant re-raise | Remove raise statement |
| LOW | relative_to() safety | Add try/except fallback |
| LOW | No file size limits | Add configurable MAX_FILE_SIZE check |

**Recommended Priority:** Fixes #1 and #2 before production (security), then #3 (debuggability).
