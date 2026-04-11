#!/usr/bin/env python3
"""Shared state helpers for applyrx GUI, helper, and CLI."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import main as core


SOURCE = "apple_music_ttml_cache"


def track_key(player: dict[str, Any]) -> tuple:
    return (
        player.get("name"),
        player.get("artist"),
        round(float(player.get("duration") or 0), 1),
    )


def active_index(lines: list[dict], position: float) -> int:
    return core.active_index(lines, position) if lines else 0


def line_at(lines: list[dict], index: int) -> dict | None:
    if not lines:
        return None
    if index < 0 or index >= len(lines):
        return None
    return lines[index]


def public_line(line: dict | None) -> dict | None:
    if not line:
        return None
    return {
        "begin": line.get("begin"),
        "end": line.get("end"),
        "text": line.get("text", ""),
    }


def build_state(
    *,
    player: dict[str, Any] | None = None,
    lines: list[dict] | None = None,
    song_id: str | None = None,
    meta: dict[str, Any] | None = None,
    error: str | None = None,
    offset: float = 0.0,
) -> dict[str, Any]:
    player = player or {}
    lines = lines or []
    position = float(player.get("position") or 0.0)
    effective_position = position + float(offset)
    idx = active_index(lines, effective_position) if lines else -1
    current = public_line(line_at(lines, idx))
    next_line = public_line(line_at(lines, idx + 1))

    return {
        "ok": bool(lines) and not error,
        "error": error or None,
        "player": {
            "running": bool(player.get("running")),
            "state": player.get("state"),
            "name": player.get("name", ""),
            "artist": player.get("artist", ""),
            "position": position,
            "duration": float(player.get("duration") or 0.0),
        },
        "match": {
            "song_id": song_id,
            "meta": meta,
            "source": SOURCE if song_id else None,
        },
        "position": {
            "raw": position,
            "offset": float(offset),
            "effective": effective_position,
        },
        "active_index": idx,
        "current_line": current,
        "next_line": next_line,
        "lines": [public_line(line) for line in lines],
    }


def read_state(offset: float = 0.0) -> dict[str, Any]:
    try:
        player = core.get_player_info()
        if not player.get("running"):
            return build_state(player=player, error="Apple Music 未在运行", offset=offset)
        if player.get("state") == "stopped":
            return build_state(player=player, error="未在播放", offset=offset)

        song_id, lines, meta = core.load_lyrics_for_player(player)
        return build_state(player=player, lines=lines, song_id=song_id, meta=meta, offset=offset)
    except BaseException as exc:
        try:
            player = core.get_player_info()
        except BaseException:
            player = {}
        return build_state(player=player, error=str(exc), offset=offset)


class PlaybackSession:
    """Small local-clock state machine for low-latency current-line watching."""

    def __init__(self, offset: float = 0.0):
        self.offset = float(offset)
        self.player: dict[str, Any] = {}
        self.lines: list[dict] = []
        self.song_id: str | None = None
        self.meta: dict[str, Any] | None = None
        self.error: str | None = None
        self.last_track_key: tuple | None = None
        self.last_full_check_at = 0.0
        self.anchor_position = 0.0
        self.anchor_monotonic = 0.0

    def effective_player(self, now: float) -> dict[str, Any]:
        player = dict(self.player or {})
        if player.get("state") == "playing":
            player["position"] = self.anchor_position + (now - self.anchor_monotonic)
        return player

    def refresh(self, force_match: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        player = core.get_player_info()
        self.player = player
        self.anchor_position = float(player.get("position") or 0.0)
        self.anchor_monotonic = now
        self.last_full_check_at = now

        if not player.get("running"):
            self.lines = []
            self.song_id = None
            self.meta = None
            self.error = "Apple Music 未在运行"
            self.last_track_key = None
            return self.state()

        if player.get("state") == "stopped":
            self.lines = []
            self.song_id = None
            self.meta = None
            self.error = "未在播放"
            self.last_track_key = None
            return self.state()

        key = track_key(player)
        if force_match or key != self.last_track_key:
            self.last_track_key = key
            self.lines = []
            self.song_id = None
            self.meta = None
            self.error = "正在匹配 Apple Music 歌词..."

        if not self.lines:
            try:
                self.song_id, self.lines, self.meta = core.load_lyrics_for_player(player)
                self.error = None
            except BaseException as exc:
                self.error = str(exc)

        return self.state()

    def tick(self, full_check_interval: float = 1.0) -> dict[str, Any]:
        now = time.monotonic()
        if self.player and now - self.last_full_check_at < full_check_interval:
            return self.state(player=self.effective_player(now))
        return self.refresh()

    def state(self, player: dict[str, Any] | None = None) -> dict[str, Any]:
        return build_state(
            player=player or self.player,
            lines=self.lines,
            song_id=self.song_id,
            meta=self.meta,
            error=self.error,
            offset=self.offset,
        )


def format_lrc(lines: list[dict]) -> str:
    def stamp(seconds: float) -> str:
        minutes = int(seconds // 60)
        rest = seconds - minutes * 60
        return f"{minutes:02d}:{rest:05.2f}"

    return "\n".join(f"[{stamp(float(line.get('begin') or 0.0))}]{line.get('text', '')}" for line in lines)
