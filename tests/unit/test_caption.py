"""Tests for the Telegram caption length clamp."""

from app.bot.bot import MAX_CAPTION_CHARS, _clamp_caption


def test_short_caption_unchanged():
    caption = "Your reimbursement report is ready."
    assert _clamp_caption(caption) == caption


def test_long_caption_trimmed_to_limit():
    long = "x" * (MAX_CAPTION_CHARS + 500)
    clamped = _clamp_caption(long)
    assert len(clamped) == MAX_CAPTION_CHARS
    assert clamped.endswith("…")
    assert clamped.startswith("x" * (MAX_CAPTION_CHARS - 1))


def test_exact_limit_unchanged():
    caption = "y" * MAX_CAPTION_CHARS
    assert _clamp_caption(caption) == caption
