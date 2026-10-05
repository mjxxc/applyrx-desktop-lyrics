#!/usr/bin/env python3
"""Print the first five timed lyric lines for the current Music.app track."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path
from typing import Optional, Sequence

from lyrics_provider import (
    Ambiguous,
    AppleMusicCacheProvider,
    CacheReadError,
    CacheScanner,
    CurrentTrack,
    InvalidMatch,
    Lyrics,
    NotFound,
)


def format_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1000)
    return "{:02d}:{:02d}:{:02d}.{:03d}".format(
        hours, minutes, whole_seconds, milliseconds
    )


def display_lyric_text(text: str) -> str:
    normalized = "".join(
        " " if unicodedata.category(character) == "Cc" else character
        for character in text
    )
    return " ".join(normalized.split())


def load_track_file(path: Path) -> CurrentTrack:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        return CurrentTrack(
            title=payload["title"],
            artist=payload["artist"],
            album=payload["album"],
            duration=float(payload["duration"]),
            catalogId=payload.get("catalogId"),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "Track JSON must contain title, artist, album, and numeric duration."
        ) from exc


def display_result(result: object) -> int:
    if isinstance(result, Lyrics):
        print("Current Track: <local track; values hidden>")
        print("Catalog ID: matched (redacted)")
        print("MATCHED")
        print("Lyrics: {} timed lines".format(len(result.lines)))
        print("Word timing: {}".format("yes" if result.hasWordTiming else "no"))
        for line in result.lines[:5]:
            print("[{} - {}] {}".format(
                format_timestamp(line.startTime),
                format_timestamp(line.endTime),
                display_lyric_text(line.text),
            ))
        return 0
    if isinstance(result, (NotFound, Ambiguous, InvalidMatch)):
        print("{}: {}".format(type(result).__name__, result.message), file=sys.stderr)
        return 1
    print("NotFound: Unsupported provider result.", file=sys.stderr)
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--track-json",
        type=Path,
        help="Use a fixed local track JSON instead of reading Music.app.",
    )
    parser.add_argument(
        "--cache-db",
        type=Path,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    try:
        track = (
            load_track_file(args.track_json)
            if args.track_json
            else AppleMusicCacheProvider.read_current_track()
        )
    except CacheReadError as exc:
        print("NotFound: {}".format(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print("InvalidMatch: {}".format(exc), file=sys.stderr)
        return 1
    scanner = CacheScanner(args.cache_db) if args.cache_db else None
    result = AppleMusicCacheProvider(scanner=scanner).get_lyrics(track)
    return display_result(result)


if __name__ == "__main__":
    raise SystemExit(main())
