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


# ── lyric text colours ──────────────────────────────────────
# (key, menu label, hex). Keys are stored in the config file.
LYRIC_COLORS = (
    ("black", "黑", "#111111"),
    ("white", "白", "#FFFFFF"),
    ("red", "红", "#FF3B30"),
    ("orange", "橙", "#FF9500"),
    ("pink", "粉", "#FF6FA8"),
    ("yellow", "黄", "#FFCC00"),
    ("green", "绿", "#34C759"),
    ("cyan", "青", "#32ADE6"),
    ("blue", "蓝", "#007AFF"),
)
DEFAULT_LYRIC_COLOR = "black"


def normalize_lyric_color(value):
    """Return a known colour key; unknown/missing values fall back to the default."""
    if isinstance(value, str):
        candidate = value.strip().lower()
        for key, _label, _hex in LYRIC_COLORS:
            if key == candidate:
                return key
    return DEFAULT_LYRIC_COLOR


def lyric_color_hex(value):
    key = normalize_lyric_color(value)
    for name, _label, hex_value in LYRIC_COLORS:
        if name == key:
            return hex_value
    return "#111111"
