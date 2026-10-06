#!/usr/bin/env python3
"""Report word-timing availability for the current Apple Music track."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyrics_provider import (
    AppleMusicCacheProvider,
    CacheReadError,
    CurrentTrack,
    Lyrics,
)


def summarize(track: CurrentTrack, result: object) -> dict:
    lyrics = result if isinstance(result, Lyrics) else None
    return {
        "title": track.title,
        "artist": track.artist,
        "hasWordTiming": lyrics.hasWordTiming if lyrics else "unknown",
        "lineCount": len(lyrics.lines) if lyrics else 0,
        "wordCount": sum(len(line.words) for line in lyrics.lines) if lyrics else 0,
    }


def main() -> int:
    provider = AppleMusicCacheProvider()
    try:
        track = provider.read_current_track()
    except CacheReadError as exc:
        print("Could not read the current Apple Music track.", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    result = provider.get_lyrics(track)
    summary = summarize(track, result)
    for key, value in summary.items():
        print(f"{key}={value}")
    return 0 if isinstance(result, Lyrics) else 1


if __name__ == "__main__":
    raise SystemExit(main())
