"""TelegramRenderer + message splitting."""

import html
import re

from bot.config import TELEGRAM_MAX_LENGTH


class TelegramRenderer:
    """Convert markdown-ish text to Telegram-compatible HTML."""

    @staticmethod
    def render(text: str) -> str:
        """Convert markdown to Telegram HTML.

        Handles: code blocks, inline code, bold, italic, strikethrough,
        headings (as bold), links, and lists.
        """
        # Protect code blocks first
        code_blocks: list[str] = []

        def _save_code_block(m: re.Match) -> str:
            lang = m.group(1) or ""
            code = html.escape(m.group(2))
            if lang:
                block = f'<pre><code class="language-{html.escape(lang)}">{code}</code></pre>'
            else:
                block = f"<pre>{code}</pre>"
            code_blocks.append(block)
            return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

        text = re.sub(
            r"```(\w*)\n?(.*?)```", _save_code_block, text, flags=re.DOTALL
        )

        # Protect inline code
        inline_codes: list[str] = []

        def _save_inline_code(m: re.Match) -> str:
            code = html.escape(m.group(1))
            inline_codes.append(f"<code>{code}</code>")
            return f"\x00INLINECODE{len(inline_codes) - 1}\x00"

        text = re.sub(r"`([^`\n]+)`", _save_inline_code, text)

        # Escape HTML in the remaining text
        text = html.escape(text)

        # Headings -> bold
        text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

        # Bold: **text** or __text__
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

        # Italic: *text* or _text_
        text = re.sub(r"(?<!\w)\*([^*]+?)\*(?!\w)", r"<i>\1</i>", text)
        text = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"<i>\1</i>", text)

        # Strikethrough: ~~text~~
        text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

        # Links: [text](url)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

        # Unordered lists
        text = re.sub(r"^[\s]*[-*]\s+", "  \u2022 ", text, flags=re.MULTILINE)

        # Ordered lists
        text = re.sub(
            r"^[\s]*(\d+)\.\s+", r"  \1. ", text, flags=re.MULTILINE
        )

        # Restore code blocks and inline code
        for i, block in enumerate(code_blocks):
            text = text.replace(f"\x00CODEBLOCK{i}\x00", block)
        for i, code in enumerate(inline_codes):
            text = text.replace(f"\x00INLINECODE{i}\x00", code)

        return text.strip()


def split_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """Split a message into chunks that fit within Telegram's limit."""
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        split_at = max_length

        # Try paragraph break
        para_break = remaining.rfind("\n\n", 0, max_length)
        if para_break > max_length // 3:
            split_at = para_break
        elif (line_break := remaining.rfind("\n", 0, max_length)) > max_length // 3:
            split_at = line_break
        elif (sentence_end := remaining.rfind(". ", 0, max_length)) > max_length // 3:
            split_at = sentence_end + 1
        elif (space := remaining.rfind(" ", 0, max_length)) > max_length // 3:
            split_at = space

        chunk = remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip()

        if chunk:
            chunks.append(chunk)

    return chunks
