#!/usr/bin/env python3
"""Read Apple Music lyric state for the GUI.

The GUI keeps rendering isolated from Apple Music cache access. This helper can
run once for debugging, or as a JSON-lines watcher for a future long-lived UI
state service.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import applyrx_state


def read_state() -> dict:
    state = applyrx_state.read_state()
    return {
        "ok": state["ok"],
        "player": state["player"],
        "song_id": state["match"]["song_id"],
        "meta": state["match"]["meta"],
        "lines": state["lines"],
        "error": state["error"],
    }


def print_state(state: dict) -> None:
    print(json.dumps(state, ensure_ascii=False), flush=True)


def state_key(state: dict) -> tuple:
    player = state.get("player") or {}
    return (
        state.get("ok"),
        player.get("name"),
        player.get("artist"),
        round(float(player.get("duration") or 0), 1),
        state.get("song_id"),
        state.get("error"),
    )


def watch(interval: float) -> int:
    last_key = None
    while True:
        state = read_state()
        key = state_key(state)
        if key != last_key:
            print_state(state)
            last_key = key
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Print one JSON state and exit.")
    parser.add_argument("--watch", action="store_true", help="Print JSON lines when track or match state changes.")
    parser.add_argument("--interval", type=float, default=1.0, help="Watch polling interval.")
    args = parser.parse_args()

    if args.watch:
        return watch(max(0.2, args.interval))

    print_state(read_state())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
