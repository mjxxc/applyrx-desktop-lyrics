#!/usr/bin/env python3
"""Run the lyric timing engine against a local JSON time-series fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from lyrics_provider import LyricLine, Lyrics
from lyrics_sync import LyricsEngine


def load_fixture(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Unable to read simulation fixture.") from exc
    if not isinstance(value, dict):
        raise ValueError("Simulation fixture must be a JSON object.")
    return value


def build_lyrics(fixture: Dict[str, Any]) -> Lyrics:
    raw_lines = fixture.get("lines")
    if not isinstance(raw_lines, list):
        raise ValueError("Fixture lines must be an array.")
    lines = []
    for item in raw_lines:
        if not isinstance(item, dict):
            raise ValueError("Each fixture line must be an object.")
        try:
            lines.append(LyricLine(
                startTime=float(item["start"]),
                endTime=float(item["end"]),
                text=str(item["text"]),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Each line requires numeric start/end and text.") from exc
    return Lyrics(
        lines=tuple(lines),
        language=fixture.get("language"),
        hasWordTiming=False,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args(argv)
    try:
        fixture = load_fixture(args.fixture)
        times = fixture.get("times")
        if not isinstance(times, list):
            raise ValueError("Fixture times must be an array.")
        engine = LyricsEngine(build_lyrics(fixture))
        for raw_time in times:
            try:
                timestamp = float(raw_time)
            except (TypeError, ValueError):
                timestamp = float("nan")
            state = engine.locate(timestamp)
            previous_text = state.previous.text if state.previous else "-"
            current_text = state.current.text if state.current else "-"
            next_text = state.next.text if state.next else "-"
            print(
                "Timestamp={:.3f} Previous={} Current={} Next={}".format(
                    state.timestamp, previous_text, current_text, next_text
                )
            )
        return 0
    except ValueError as exc:
        print("InvalidMatch: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
