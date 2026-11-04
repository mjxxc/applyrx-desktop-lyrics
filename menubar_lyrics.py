"""Pure logic for the menu bar lyric capsule.

This module deliberately has no AppKit import so the formatting and visibility
rules stay unit-testable without a GUI session. The AppKit view lives in
``menubar_lyrics_capsule.py``.
"""

from __future__ import annotations

MODE_CAPSULE = "capsule"
MODE_TEXT = "text"
MODE_ICON = "icon"
MODES = (MODE_CAPSULE, MODE_TEXT, MODE_ICON)
DEFAULT_MODE = MODE_CAPSULE

MODE_LABELS = {
    MODE_CAPSULE: "图标 + 悬停歌词",
    MODE_TEXT: "菜单栏直接显示文字",
    MODE_ICON: "仅图标",
}

DEFAULT_MAX_CHARS = 26
MAX_CAPSULE_CHARS = 40


def normalize_mode(value, legacy_visible=None):
    """Return a valid menu bar lyric mode.

    ``menubar_lyrics_mode`` is the v0.2.5 key. Older configs only know
    ``menubar_lyrics_visible``; a user who had turned the menu bar lyric off
    keeps the menu bar item but gets the icon-only mode.
    """
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in MODES:
            return candidate
    if legacy_visible is False:
        return MODE_ICON
    return DEFAULT_MODE


def format_line(text, limit=DEFAULT_MAX_CHARS):
    """One-line lyric text, whitespace collapsed and truncated with an ellipsis."""
    normalized = " ".join(str(text or "").split())
    if limit <= 1:
        return ""
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def capsule_copy(current, next_line, limit=DEFAULT_MAX_CHARS):
    """The two rows shown in the hover capsule: current line, then the next one."""
    return format_line(current, limit), format_line(next_line, limit)


def should_show_capsule(playing, has_lyrics, mode):
    """The capsule is a quiet, playback-only surface."""
    return mode == MODE_CAPSULE and bool(playing) and bool(has_lyrics)


def status_item_copy(mode, current, next_line="", limit=DEFAULT_MAX_CHARS):
    """Return ``(uses_icon, title)`` for the status item in the given mode."""
    if mode == MODE_TEXT:
        if current:
            return False, format_line(current, 28)
        return False, "applyrx"
    return True, ""
