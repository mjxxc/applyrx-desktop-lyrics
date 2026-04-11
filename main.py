#!/usr/bin/env python3
"""applyrx — Apple Music 实时歌词终端显示"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

# 加载底层模块
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from rich.align import Align
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
except ModuleNotFoundError:
    venv_python = SCRIPT_DIR / "venv/bin/python"
    if venv_python.exists() and Path(sys.prefix).resolve() != (SCRIPT_DIR / "venv").resolve():
        os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])
    raise

from apple_music_ttml import fetch, get_recent_cached_requests, parse_ttml_lines

console = Console()

TRACK_RE = re.compile(r"id=(\d+)")
CONTEXT = 4  # 当前行上下各显示几行


# ── 播放器信息 ──────────────────────────────────────────────

def get_player_info() -> dict[str, Any]:
    script = """
set delim to "|||"
if application "Music" is running then
  tell application "Music"
    if not (exists current track) then
      return "running" & delim & "stopped" & delim & "" & delim & "" & delim & "0" & delim & "0"
    end if
    set track_name to my safe_text(name of current track)
    set track_artist to my safe_text(artist of current track)
    set track_duration to duration of current track
    return "running" & delim & (player state as text) & delim & track_name & delim & track_artist & delim & (player position as text) & delim & (track_duration as text)
  end tell
else
  return "not_running"
end if

on safe_text(v)
  try
    return v as text
  on error
    try
      return v as string
    on error
      return ""
    end try
  end try
end safe_text
"""
    r = subprocess.run(
        ["osascript"],
        input=script,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        return {"running": False}
    out = r.stdout.strip()
    if out == "not_running":
        return {"running": False}
    parts = out.split("|||", 5)
    while len(parts) < 6:
        parts.append("0")
    return {
        "running": True,
        "state": parts[1],
        "name": parts[2],
        "artist": parts[3],
        "position": float(parts[4] or 0.0),
        "duration": float(parts[5] or 0.0),
    }


# ── 歌词加载 ──────────────────────────────────────────────

def parse_timecode(value: str) -> float:
    total = 0.0
    for part in value.split(":"):
        total = total * 60 + float(part)
    return total


ITUNES_META_CACHE: dict[str, dict[str, Any] | None] = {}

TRAD_TO_SIMP = str.maketrans({
    "親": "亲", "愛": "爱", "這": "这", "裡": "里", "裏": "里", "麼": "么",
    "為": "为", "會": "会", "聽": "听", "說": "说", "過": "过", "還": "还",
    "讓": "让", "總": "总", "個": "个", "夢": "梦", "見": "见", "倆": "俩",
    "問": "问", "現": "现", "給": "给", "機": "机", "開": "开", "聲": "声",
    "輕": "轻", "轉": "转", "離": "离", "歡": "欢", "關": "关", "緊": "紧",
    "處": "处", "間": "间", "嗎": "吗", "時": "时", "氣": "气", "東": "东",
    "萬": "万", "與": "与", "妳": "你", "夠": "够", "動": "动", "記": "记",
})


def norm(s: str) -> str:
    s = s.translate(TRAD_TO_SIMP).lower().strip()
    s = re.sub(r"[\s\-_·・'’\"“”()\[\]（）。,.，:：!！?？]+", "", s)
    return s


def lookup_itunes_meta(song_id: str) -> dict[str, Any] | None:
    """按 Apple song id 读取 iTunes 官方元数据，用于验证歌词是否属于当前歌。"""
    if song_id in ITUNES_META_CACHE:
        return ITUNES_META_CACHE[song_id]

    # Apple Music CN catalog ids may not resolve in the default iTunes storefront.
    for country in ("cn", "tw", "us", ""):
        country_param = f"&country={country}" if country else ""
        try:
            req = urllib.request.Request(
                f"https://itunes.apple.com/lookup?id={song_id}&entity=song{country_param}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception:
            continue

        for item in data.get("results", []):
            if str(item.get("trackId", "")) == song_id:
                meta = {
                    "id": song_id,
                    "name": item.get("trackName", ""),
                    "artist": item.get("artistName", ""),
                    "album": item.get("collectionName", ""),
                    "duration": (item.get("trackTimeMillis") or 0) / 1000,
                }
                ITUNES_META_CACHE[song_id] = meta
                return meta

    ITUNES_META_CACHE[song_id] = None
    return None


def meta_matches_player(meta: dict[str, Any], player: dict[str, Any], unique_duration: bool) -> bool:
    duration = float(player.get("duration") or 0)
    meta_duration = float(meta.get("duration") or 0)
    duration_close = duration > 0 and meta_duration > 0 and abs(duration - meta_duration) <= 2.0

    player_name = norm(player.get("name", ""))
    player_artist = norm(player.get("artist", ""))
    meta_name = norm(meta.get("name", ""))
    meta_artist = norm(meta.get("artist", ""))

    title_close = bool(player_name and meta_name and (player_name == meta_name or player_name in meta_name or meta_name in player_name))
    artist_close = bool(player_artist and meta_artist and (player_artist == meta_artist or player_artist in meta_artist or meta_artist in player_artist))

    # 标题/艺人能对上时最稳；中文曲名被 Apple 返回英文名时，用“唯一时长命中”兜底。
    return duration_close and (title_close or artist_close or unique_duration)


def _parse_entry(url: str, headers: dict) -> tuple[str, list[dict]]:
    body = fetch(url, headers)
    payload = json.loads(body)
    ttml = payload.get("ttml")
    if not ttml:
        raise RuntimeError("歌词响应中没有 TTML 数据")
    m = TRACK_RE.search(url)
    song_id = m.group(1) if m else "unknown"
    lines = [
        {
            "begin": parse_timecode(l["begin"]),
            "end": parse_timecode(l["end"]) if l["end"] else None,
            "text": l["text"],
        }
        for l in parse_ttml_lines(ttml)
    ]
    return song_id, lines


def load_lyrics_for_player(player: dict[str, Any]) -> tuple[str, list[dict], dict[str, Any]]:
    """只加载和当前播放歌明显匹配的缓存歌词，避免换歌后串歌词。"""
    entries = get_recent_cached_requests(limit=50)
    if not entries:
        raise RuntimeError("缓存中没有歌词请求，请在 Apple Music 打开歌词面板")

    candidates: list[tuple[str, str, dict[str, str], dict[str, Any]]] = []
    duration = float(player.get("duration") or 0)
    duration_hits = 0

    for url, headers, _ in entries:
        m = TRACK_RE.search(url)
        if not m:
            continue
        candidate_id = m.group(1)
        meta = lookup_itunes_meta(candidate_id)
        if not meta:
            continue
        if duration > 0 and meta.get("duration") and abs(duration - float(meta["duration"])) <= 2.0:
            duration_hits += 1
        candidates.append((candidate_id, url, headers, meta))

    for candidate_id, url, headers, meta in candidates:
        if meta_matches_player(meta, player, unique_duration=(duration_hits == 1)):
            song_id, lines = _parse_entry(url, headers)
            return song_id, lines, meta

    raise RuntimeError("没有找到和当前歌名/时长匹配的歌词缓存，请在 Apple Music 打开当前歌的歌词面板")


def active_index(lines: list[dict], position: float) -> int:
    idx = 0
    for i, line in enumerate(lines):
        if line["begin"] <= position:
            idx = i
        else:
            break
    return idx


# ── UI 渲染 ──────────────────────────────────────────────

def fmt_time(s: float) -> str:
    return f"{int(s // 60):02d}:{int(s % 60):02d}"


def build_panel(player: dict, lines: list[dict]) -> Panel:
    name = player.get("name", "")
    artist = player.get("artist", "")
    position = player.get("position", 0.0)
    state = player.get("state", "")

    # ── 标题 ──
    header = Text(justify="center")
    header.append("♪  ", style="cyan")
    header.append(name, style="bold white")
    header.append("  —  ", style="dim white")
    header.append(artist, style="cyan")
    if state == "paused":
        header.append("  ⏸", style="dim yellow")

    # ── 歌词 ──
    lyric_text = Text(justify="center")

    if not lines:
        lyric_text.append("\n请先在 Apple Music 里打开歌词面板\n", style="dim white")
    else:
        idx = active_index(lines, position)
        start = max(0, idx - CONTEXT)
        end = min(len(lines), idx + CONTEXT + 1)

        for i in range(start, end):
            word = lines[i]["text"]
            diff = i - idx
            if diff < 0:
                # 已唱过
                alpha = 1 - (abs(diff) / (CONTEXT + 1))
                style = f"rgb({int(180*alpha)},{int(180*alpha)},{int(180*alpha)})"
                lyric_text.append(f"{word}\n", style=style)
            elif diff == 0:
                # 当前行
                lyric_text.append(f"{word}\n", style="bold bright_white")
            else:
                # 即将唱
                alpha = 1 - (diff / (CONTEXT + 1))
                style = f"rgb({int(160*alpha)},{int(160*alpha)},{int(160*alpha)})"
                lyric_text.append(f"{word}\n", style=style)

    # ── 进度条 ──
    dur_raw = player.get("duration", 0.0)

    bar_width = max(10, console.width - 22)
    pct = position / dur_raw if dur_raw else 0
    filled = int(bar_width * pct)
    bar_str = "━" * filled + "╌" * (bar_width - filled)

    progress = Text(justify="center")
    progress.append(f"{fmt_time(position)} ", style="dim white")
    progress.append(bar_str[:filled], style="cyan")
    progress.append(bar_str[filled:], style="dim white")
    progress.append(f" {fmt_time(dur_raw)}", style="dim white")

    content = Group(
        Align.center(header),
        Text(""),
        Align.center(lyric_text),
        Text(""),
        progress,
    )

    return Panel(
        content,
        box=box.ROUNDED,
        border_style="dim blue",
        padding=(1, 3),
        subtitle="[dim]q 退出[/dim]",
    )


def build_status(msg: str, sub: str = "") -> Panel:
    t = Text(justify="center")
    t.append(msg, style="dim white")
    if sub:
        t.append(f"\n{sub}", style="dim cyan")
    return Panel(Align.center(t), box=box.ROUNDED,
                 border_style="dim blue", padding=(2, 4))


# ── 主循环 ──────────────────────────────────────────────

def run() -> None:
    song_id: str | None = None
    lines: list[dict] = []
    matched_meta: dict[str, Any] | None = None
    err_msg: str | None = None
    last_key: tuple[str, str] = ("", "")

    with Live(console=console, refresh_per_second=4, screen=True) as live:
        while True:
            # 读播放器
            player = get_player_info()

            if not player.get("running"):
                live.update(build_status("Apple Music 未在运行"))
                time.sleep(1)
                continue

            if player.get("state") == "stopped":
                live.update(build_status("未在播放"))
                time.sleep(1)
                continue

            # 换歌判定：歌名+艺术家组合变化
            name = player.get("name", "")
            artist = player.get("artist", "")
            key = (name, artist)
            if key != last_key:
                last_key = key
                lines = []
                matched_meta = None
                err_msg = None
                song_id = None

            # 若没有歌词，持续尝试加载
            if not lines:
                try:
                    new_id, new_lines, new_meta = load_lyrics_for_player(player)
                    song_id = new_id
                    lines = new_lines
                    matched_meta = new_meta
                    err_msg = None
                except BaseException as e:
                    err_msg = str(e)
                    lines = []

            if err_msg and not lines:
                live.update(build_status(
                    f"♪  {player.get('name', '')}  —  {player.get('artist', '')}",
                    err_msg
                ))
            else:
                live.update(build_panel(player, lines))

            time.sleep(0.25)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        console.print("\n[dim]已退出[/dim]")
