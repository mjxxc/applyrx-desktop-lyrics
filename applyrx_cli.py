#!/usr/bin/env python3
"""CLI interface for Applyrx Apple Music lyrics."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
VENV_DIR = SCRIPT_DIR / "venv"
VENV_PYTHON = VENV_DIR / "bin/python"

if VENV_PYTHON.exists() and Path(sys.prefix).resolve() != VENV_DIR.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

import applyrx_state


def print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def command_state(args) -> int:
    print_json(applyrx_state.read_state(offset=args.offset))
    return 0


def command_lyrics(args) -> int:
    state = applyrx_state.read_state(offset=args.offset)
    if args.format == "lrc":
        if state["ok"]:
            print(applyrx_state.format_lrc(state["lines"]))
            return 0
        print(state["error"] or "No lyrics", file=sys.stderr)
        return 1

    print_json(state)
    return 0 if state["ok"] else 1


def command_current_line(args) -> int:
    state = applyrx_state.read_state(offset=args.offset)
    current = state.get("current_line")
    if not state["ok"] or not current:
        if not args.quiet:
            print(state["error"] or "No current lyric", file=sys.stderr)
        return 1
    print(current.get("text", ""))
    return 0


def watch_key(state: dict) -> tuple:
    player = state.get("player") or {}
    current = state.get("current_line") or {}
    return (
        state.get("ok"),
        state.get("error"),
        player.get("name"),
        player.get("artist"),
        round(float(player.get("duration") or 0), 1),
        state.get("active_index"),
        current.get("text"),
    )


def command_watch(args) -> int:
    session = applyrx_state.PlaybackSession(offset=args.offset)
    last_key = None
    full_interval = max(0.2, args.full_interval)
    interval = max(0.05, args.interval)

    while True:
        state = session.tick(full_check_interval=full_interval)
        key = watch_key(state)
        if key != last_key:
            print_json(state)
            last_key = key
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offset", type=float, default=0.8, help="Lyric offset in seconds. Positive shows lyrics earlier.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    state_parser = subparsers.add_parser("state", help="Print current playback and lyric state as JSON.")
    state_parser.set_defaults(func=command_state)

    lyrics_parser = subparsers.add_parser("lyrics", help="Print full lyrics for the current song.")
    lyrics_parser.add_argument("--format", choices=("json", "lrc"), default="json")
    lyrics_parser.set_defaults(func=command_lyrics)

    watch_parser = subparsers.add_parser("watch", help="Watch lyric changes as JSON Lines.")
    watch_parser.add_argument("--interval", type=float, default=0.12, help="Local render polling interval.")
    watch_parser.add_argument("--full-interval", type=float, default=1.0, help="Apple Music progress calibration interval.")
    watch_parser.set_defaults(func=command_watch)

    current_parser = subparsers.add_parser("current-line", help="Print only the current lyric line.")
    current_parser.add_argument("--quiet", action="store_true", help="Do not print errors to stderr.")
    current_parser.set_defaults(func=command_current_line)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
