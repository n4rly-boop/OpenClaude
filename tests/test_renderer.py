"""Tests for TelegramRenderer and split_message."""

from bot.renderer import TelegramRenderer, split_message


class TestTelegramRenderer:
    def test_bold(self):
        assert "<b>hello</b>" in TelegramRenderer.render("**hello**")

    def test_italic(self):
        assert "<i>hello</i>" in TelegramRenderer.render("*hello*")

    def test_code_block(self):
        result = TelegramRenderer.render("```\nprint('hi')\n```")
        assert "<pre>" in result
        assert "print(&#x27;hi&#x27;)" in result or "print('hi')" in result

    def test_code_block_with_language(self):
        result = TelegramRenderer.render("```python\nx = 1\n```")
        assert 'class="language-python"' in result

    def test_inline_code(self):
        result = TelegramRenderer.render("use `foo()` here")
        assert "<code>foo()</code>" in result

    def test_link(self):
        result = TelegramRenderer.render("[click](https://example.com)")
        assert '<a href="https://example.com">click</a>' in result

    def test_heading(self):
        result = TelegramRenderer.render("## Title")
        assert "<b>Title</b>" in result

    def test_unordered_list(self):
        result = TelegramRenderer.render("- item one\n- item two")
        assert "\u2022 item one" in result
        assert "\u2022 item two" in result

    def test_empty_string(self):
        assert TelegramRenderer.render("") == ""

    def test_special_chars_escaped(self):
        result = TelegramRenderer.render("a < b & c > d")
        assert "&lt;" in result
        assert "&amp;" in result
        assert "&gt;" in result

    def test_strikethrough(self):
        assert "<s>deleted</s>" in TelegramRenderer.render("~~deleted~~")


class TestSplitMessage:
    def test_no_split_needed(self):
        assert split_message("short", max_length=100) == ["short"]

    def test_splits_at_paragraph(self):
        text = "A" * 50 + "\n\n" + "B" * 50
        chunks = split_message(text, max_length=80)
        assert len(chunks) >= 2
        assert chunks[0].strip().startswith("A")
        assert chunks[1].strip().startswith("B")

    def test_splits_at_line_break(self):
        text = "A" * 50 + "\n" + "B" * 50
        chunks = split_message(text, max_length=80)
        assert len(chunks) >= 2

    def test_long_word_forced_split(self):
        text = "A" * 200
        chunks = split_message(text, max_length=100)
        assert len(chunks) >= 2
        total = sum(len(c) for c in chunks)
        assert total == 200
