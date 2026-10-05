#!/usr/bin/env python3
"""Report real Music.app playback and locally cached lyric synchronization."""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Sequence, Tuple

from lyrics_provider import AppleMusicCacheProvider, CurrentTrack
from lyrics_sync import (
    CurrentTrackManager,
    ManagerState,
    NowPlayingWatcher,
    PlaybackSnapshot,
    track_identity,
)


def format_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1000)
    return "{:02d}:{:02d}:{:02d}.{:03d}".format(
        hours, minutes, whole_seconds, milliseconds
    )


def line_text(line) -> str:
    return line.text if line is not None else "-"


def track_summary(track: Optional[CurrentTrack]) -> str:
    if track is None:
        return "track=unavailable"
    return "title={!r} artist={!r} album={!r}".format(
        track.title, track.artist, track.album
    )


def state_signature(state: ManagerState) -> Tuple[object, ...]:
    position = state.lyricPosition
    return (
        track_identity(state.snapshot.track),
        state.snapshot.playbackState,
        state.snapshot.positionAvailable,
        state.snapshot.positionHeld,
        state.matchStatus,
        state.catalogId,
        position.currentIndex,
        position.previous.text if position.previous else None,
        position.current.text if position.current else None,
        position.next.text if position.next else None,
        state.message,
        state.snapshot.seeked,
    )


def report_snapshot(
    state: ManagerState,
    changed: bool,
    track_changed: bool,
) -> None:
    snapshot = state.snapshot
    if track_changed:
        print("Track: " + track_summary(snapshot.track))
    if changed and state.catalogId:
        print("Catalog ID: " + state.catalogId)
    elif changed and state.matchStatus in ("loading", "timeout"):
        print("Catalog ID: " + ("resolving locally" if state.matchStatus == "loading"
                               else "unresolved"))
    print(
        "position={} state={} match={} index={}".format(
            format_timestamp(snapshot.position) if snapshot.positionAvailable
            else "unavailable",
            snapshot.playbackState,
            state.matchStatus,
            "-" if state.lyricPosition.currentIndex is None
            else state.lyricPosition.currentIndex,
        )
    )
    if changed:
        print(
            "Previous={} | Current={} | Next={}".format(
                line_text(state.lyricPosition.previous),
                line_text(state.lyricPosition.current),
                line_text(state.lyricPosition.next),
            )
        )
    if snapshot.seeked:
        print("Seek detected at " + format_timestamp(snapshot.position))
    if snapshot.positionHeld:
        print("Position held at last Music sample while paused; no elapsed-time estimate.")
    if state.message and state.matchStatus not in ("loading",):
        print("Status: " + state.message)
    sys.stdout.flush()


def run_live(
    interval: float = 0.5,
    duration: Optional[float] = None,
    max_ticks: Optional[int] = None,
) -> int:
    provider = AppleMusicCacheProvider()
    watcher = NowPlayingWatcher()
    start = time.monotonic()
    count = 0
    last_signature = None
    last_track_key = None
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="lyrics-provider") as executor:
            manager = CurrentTrackManager(
                provider,
                executor=executor,
                provider_timeout=8.0,
            )
            try:
                while True:
                    snapshot = watcher.poll()
                    state = manager.update(snapshot)
                    signature = state_signature(state)
                    changed = signature != last_signature
                    track_key = track_identity(snapshot.track)
                    track_changed = track_key != last_track_key
                    report_snapshot(state, changed, track_changed)
                    last_signature = signature
                    last_track_key = track_key
                    count += 1
                    if max_ticks is not None and count >= max_ticks:
                        return 0
                    if duration is not None and time.monotonic() - start >= duration:
                        return 0
                    time.sleep(max(0.05, interval))
            finally:
                manager.close()
    except KeyboardInterrupt:
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--duration", type=float, help="Stop after this many seconds.")
    parser.add_argument("--max-ticks", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.duration is not None and args.duration <= 0:
        parser.error("--duration must be positive.")
    if args.max_ticks is not None and args.max_ticks <= 0:
        parser.error("--max-ticks must be positive.")
    return run_live(
        interval=args.interval,
        duration=args.duration,
        max_ticks=args.max_ticks,
    )


if __name__ == "__main__":
    raise SystemExit(main())
