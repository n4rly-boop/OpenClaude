"""Reaction directives — detect [react:emoji] in Claude output and apply via Telegram API."""

import logging
import re

logger = logging.getLogger(__name__)

# Pattern to match [react:emoji] directives
_REACT_PATTERN = re.compile(r"\[react:([^\]]+)\]")

# Telegram custom emoji IDs are not needed — we use standard emoji strings.
# Telegram's setMessageReaction accepts ReactionTypeEmoji objects.


def extract_reactions(text: str) -> tuple[str, list[str]]:
    """Extract [react:emoji] directives from text.

    Returns (cleaned_text, list_of_emoji_strings).
    """
    reactions = _REACT_PATTERN.findall(text)
    if not reactions:
        return text, []
    cleaned = _REACT_PATTERN.sub("", text).strip()
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for r in reactions:
        r = r.strip()
        if r and r not in seen:
            seen.add(r)
            unique.append(r)
    return cleaned, unique


async def apply_reactions(bot, chat_id: int, message_id: int, emojis: list[str]) -> None:
    """Apply emoji reactions to a message via Telegram API.

    Uses setMessageReaction with ReactionTypeEmoji.
    Only the first reaction is applied (Telegram limits reactions per bot).
    """
    if not emojis:
        return

    try:
        from telegram import ReactionTypeEmoji
        # Telegram only allows one reaction per bot call
        reaction = ReactionTypeEmoji(emoji=emojis[0])
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[reaction],
        )
        logger.debug("Applied reaction %s to message %d in chat %d", emojis[0], message_id, chat_id)
    except Exception as e:
        err_str = str(e)
        if "Retry in" in err_str or "Too Many Requests" in err_str or "Flood control" in err_str:
            logger.warning("Flood control applying reaction %s: %s", emojis[0], e)
        else:
            logger.debug("Failed to apply reaction %s: %s", emojis[0], e)
