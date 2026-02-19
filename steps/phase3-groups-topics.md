# Phase 3: Groups & Topics Support

## Goal
Make the bot work in Telegram group chats (respond only when mentioned or replied to) and support forum topics (each topic = separate Claude session).

## Step 3.1 — Session Key Refactor

Current: `session_key = str(user_id)`
New: `session_key = f"{chat_id}:{thread_id}:{user_id}"`

This gives:
- DMs: `123456:0:123456` (chat_id == user_id, thread_id 0)
- Group: `−100123:0:456789` (group chat_id, no topic)
- Topic: `−100123:42:456789` (group chat_id, topic thread_id 42)

Change `get_session_id`, `set_session_id`, `clear_session` to accept `chat_id`, `thread_id`, `user_id`.

## Step 3.2 — Group Message Filtering

Add logic to decide whether to respond in group chats:

```python
def should_respond(update: Update, bot_username: str) -> bool:
    chat = update.effective_chat

    # Always respond in private DMs
    if chat.type == "private":
        return True

    msg = update.message

    # Respond if bot is mentioned (@bot_username)
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                mention = msg.text[entity.offset:entity.offset + entity.length]
                if mention.lower() == f"@{bot_username.lower()}":
                    return True

    # Respond if message is a reply to bot's message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.username == bot_username:
            return True

    return False
```

## Step 3.3 — Thread/Topic ID Extraction

```python
def get_thread_id(update: Update) -> int:
    """Get the forum topic thread ID, or 0 for non-forum messages."""
    msg = update.message
    return msg.message_thread_id if msg and msg.message_thread_id else 0
```

## Step 3.4 — Update All Handlers

Every handler needs:
1. Call `should_respond()` before processing (skip if False in groups)
2. Extract `chat_id` and `thread_id`
3. Pass to `call_claude()` with new session key
4. When replying in group topics, use `message_thread_id` parameter

## Step 3.5 — Reply in Topics

When the bot sends a message in a forum group, it must include `message_thread_id`:
```python
await update.message.reply_text(
    chunk,
    parse_mode=ParseMode.HTML,
    message_thread_id=get_thread_id(update),
)
```

## Step 3.6 — Strip Bot Mention from Message

In group chats, remove `@bot_username` from the message before passing to Claude:
```python
text = text.replace(f"@{bot_username}", "").strip()
```

## Files to Change
- `telegram-bot.py` — session key refactor, group filtering, topic support, mention stripping
