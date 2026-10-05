"""Playback watcher and time-based lyric synchronization state machine."""

from __future__ import annotations

import math
import time
from concurrent.futures import Executor, Future
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence, Tuple, Union

from lyrics_provider import (
    Ambiguous,
    CurrentTrack,
    InvalidMatch,
    LyricLine,
    Lyrics,
    LyricsProvider,
    NotFound,
    ProviderResult,
    TrackMetadata,
    normalize_text,
)


@dataclass(frozen=True)
class PlaybackSnapshot:
    track: Optional[CurrentTrack]
    position: float
    playbackState: str
    sampledAt: float
    trackChanged: bool = False
    stateChanged: bool = False
    positionChanged: bool = False
    seeked: bool = False
    error: Optional[str] = None
    positionAvailable: bool = True
    positionHeld: bool = False


@dataclass(frozen=True)
class LyricsPosition:
    timestamp: float
    currentIndex: Optional[int]
    current: Optional[LyricLine]
    previous: Optional[LyricLine]
    next: Optional[LyricLine]


@dataclass(frozen=True)
class ManagerState:
    snapshot: PlaybackSnapshot
    lyrics: Optional[Lyrics]
    lyricPosition: LyricsPosition
    matchStatus: str
    message: Optional[str]
    catalogId: Optional[str] = None


class Clock(Protocol):
    def __call__(self) -> float:
        ...


class NowPlayingWatcher:
    """Poll Music via an injected read-only snapshot reader."""

    def __init__(
        self,
        reader: Optional[Callable[[], CurrentTrack]] = None,
        clock: Clock = time.monotonic,
    ):
        self.reader = reader or self._read_music_track
        self.clock = clock
        self._previous_key: Optional[Tuple[str, ...]] = None
        self._previous_state: Optional[str] = None
        self._previous_position: Optional[float] = None
        self._previous_sampled_at: Optional[float] = None

    @staticmethod
    def _read_music_track() -> CurrentTrack:
        return TrackMetadataReader.read()

    def poll(self) -> PlaybackSnapshot:
        try:
            track = self.reader()
            state = (track.playbackState or "unknown").casefold()
            position = float(track.playbackPosition)
            if not math.isfinite(position) or position < 0:
                raise ValueError("invalid playback position")
            key = track_identity(track)
            position_available = True
            position_held = False
            if state == "paused" and position == 0:
                if (key == self._previous_key
                        and self._previous_position is not None):
                    position = self._previous_position
                    position_held = True
                else:
                    position_available = False
            sampled_at = self.clock()
            position_changed = (
                position_available
                and
                self._previous_position is not None
                and not math.isclose(position, self._previous_position, abs_tol=0.05)
            )
            seeked = False
            if (position_available and position_changed
                    and self._previous_position is not None
                    and self._previous_sampled_at is not None):
                elapsed = max(0.0, sampled_at - self._previous_sampled_at)
                if self._previous_state == "playing":
                    expected = self._previous_position + elapsed
                    seeked = abs(position - expected) > 1.5
                elif self._previous_state == "paused":
                    seeked = abs(position - self._previous_position) > 0.25
            snapshot = PlaybackSnapshot(
                track=track,
                position=position,
                playbackState=state,
                sampledAt=sampled_at,
                trackChanged=self._previous_key is not None and key != self._previous_key,
                stateChanged=self._previous_state is not None and state != self._previous_state,
                positionChanged=position_changed,
                seeked=seeked,
                positionAvailable=position_available,
                positionHeld=position_held,
            )
            self._previous_key = key
            self._previous_state = state
            self._previous_position = position if position_available else None
            self._previous_sampled_at = sampled_at
            return snapshot
        except Exception:
            self._previous_key = None
            self._previous_state = None
            self._previous_position = None
            self._previous_sampled_at = None
            return PlaybackSnapshot(
                track=None,
                position=0.0,
                playbackState="unavailable",
                sampledAt=self.clock(),
                error="Unable to read current Music playback state.",
            )


class TrackMetadataReader:
    """Lazy bridge to the provider's read-only AppleScript reader."""

    @staticmethod
    def read() -> CurrentTrack:
        from lyrics_provider import AppleMusicCacheProvider, CacheReadError

        try:
            return AppleMusicCacheProvider.read_current_track()
        except CacheReadError as exc:
            raise RuntimeError("Music.app playback snapshot is unavailable.") from exc


class LyricsEngine:
    """Pure time-to-lyric lookup; it never advances an index by itself."""

    def __init__(self, lyrics: Optional[Lyrics] = None):
        self.lyrics: Optional[Lyrics] = None
        self.valid = False
        if lyrics is not None:
            self.set_lyrics(lyrics)

    def clear(self) -> None:
        self.lyrics = None
        self.valid = False

    def set_lyrics(self, lyrics: Optional[Lyrics]) -> bool:
        self.lyrics = lyrics
        self.valid = self._valid_lines(lyrics.lines if lyrics else ())
        return self.valid

    @staticmethod
    def _valid_lines(lines: Sequence[LyricLine]) -> bool:
        if not lines:
            return False
        previous_start = -math.inf
        previous_end = -math.inf
        for line in lines:
            if (isinstance(line.startTime, bool) or isinstance(line.endTime, bool)
                    or not isinstance(line.startTime, (int, float))
                    or not isinstance(line.endTime, (int, float))):
                return False
            try:
                start, end = float(line.startTime), float(line.endTime)
            except (TypeError, ValueError):
                return False
            if (not math.isfinite(start) or not math.isfinite(end)
                    or start < 0 or end <= start or start < previous_start
                    or start < previous_end):
                return False
            previous_start = start
            previous_end = end
            if not isinstance(line.words, (tuple, list)):
                return False
            for word in line.words:
                if (isinstance(word.startTime, bool) or isinstance(word.endTime, bool)
                        or not isinstance(word.startTime, (int, float))
                        or not isinstance(word.endTime, (int, float))):
                    return False
                try:
                    word_start = float(word.startTime)
                    word_end = float(word.endTime)
                except (TypeError, ValueError):
                    return False
                if (not math.isfinite(word_start) or not math.isfinite(word_end)
                        or word_start < start or word_end > end
                        or word_end <= word_start):
                    return False
        return True

    def locate(self, timestamp: float) -> LyricsPosition:
        try:
            now = float(timestamp)
        except (TypeError, ValueError):
            now = math.nan
        if not math.isfinite(now) or now < 0 or not self.valid or not self.lyrics:
            return LyricsPosition(now, None, None, None, None)

        lines = self.lyrics.lines
        previous = None
        next_line = None
        for index, line in enumerate(lines):
            if line.startTime <= now < line.endTime:
                previous = lines[index - 1] if index > 0 else None
                next_line = lines[index + 1] if index + 1 < len(lines) else None
                return LyricsPosition(now, index, line, previous, next_line)
            if line.endTime <= now:
                previous = line
            elif line.startTime > now:
                next_line = line
                break
        return LyricsPosition(now, None, None, previous, next_line)


class CurrentTrackManager:
    """Clear-on-change manager with stale asynchronous result protection."""

    def __init__(
        self,
        provider: LyricsProvider,
        executor: Optional[Executor] = None,
        clock: Clock = time.monotonic,
        provider_timeout: float = 8.0,
    ):
        self.provider = provider
        self.executor = executor
        self.clock = clock
        self.provider_timeout = max(0.1, float(provider_timeout))
        self.engine = LyricsEngine()
        self.currentTrack: Optional[CurrentTrack] = None
        self.currentKey: Optional[Tuple[str, ...]] = None
        self.currentCatalogId: Optional[str] = None
        self.matchStatus = "notFound"
        self.message: Optional[str] = None
        self._generation = 0
        self._pending: Optional[Tuple[int, Tuple[str, ...], Future, float]] = None
        self._last_snapshot: Optional[PlaybackSnapshot] = None

    def update(self, snapshot: PlaybackSnapshot) -> ManagerState:
        self._last_snapshot = snapshot
        track = snapshot.track
        key = track_identity(track) if track is not None else None
        if key != self.currentKey:
            self._switch_track(track, key)
        self._resolve_pending()

        playback_position = snapshot.position if track is not None else 0.0
        lyric_position = self.engine.locate(
            playback_position if snapshot.positionAvailable else math.nan
        )
        return ManagerState(
            snapshot=snapshot,
            lyrics=self.engine.lyrics if self.engine.valid else None,
            lyricPosition=lyric_position,
            matchStatus=self.matchStatus,
            message=self.message or snapshot.error,
            catalogId=self.currentCatalogId or (track.catalogId if track else None),
        )

    def _switch_track(
        self,
        track: Optional[CurrentTrack],
        key: Optional[Tuple[str, ...]],
    ) -> None:
        self._generation += 1
        if self._pending is not None:
            self._pending[2].cancel()
        self.currentKey = key
        self.currentTrack = track
        self.currentCatalogId = track.catalogId if track else None
        self.engine.clear()
        self.matchStatus = "notFound"
        self.message = None
        self._pending = None
        if track is None:
            self.message = "No current track."
            return

        generation = self._generation
        if self.executor is None:
            try:
                result = self.provider.get_lyrics(track)
            except Exception:
                result = InvalidMatch("Lyrics provider failed.")
            self._apply_result(result, generation, key)
            return
        try:
            future = self.executor.submit(self.provider.get_lyrics, track)
        except Exception:
            self.matchStatus = "invalid"
            self.message = "Lyrics provider request could not be started."
            return
        self._pending = (generation, key, future, self.clock())
        self.matchStatus = "loading"

    def _resolve_pending(self) -> None:
        pending = self._pending
        if pending is None:
            return
        generation, key, future, started_at = pending
        if self.clock() - started_at > self.provider_timeout:
            self._pending = None
            self.matchStatus = "timeout"
            self.message = "Lyrics provider timed out; no previous lyrics are retained."
            return
        if not future.done():
            return
        self._pending = None
        if generation != self._generation or key != self.currentKey:
            return
        try:
            result = future.result()
        except Exception:
            result = InvalidMatch("Lyrics provider failed.")
        self._apply_result(result, generation, key)

    def _apply_result(
        self,
        result: ProviderResult,
        generation: int,
        key: Optional[Tuple[str, ...]],
    ) -> None:
        if generation != self._generation or key != self.currentKey:
            return
        if isinstance(result, Lyrics):
            expected_catalog_id = (
                self.currentTrack.catalogId if self.currentTrack else None
            )
            if (not result.catalogId
                    or not result.catalogId.isdigit()
                    or (expected_catalog_id and result.catalogId != expected_catalog_id)):
                self.engine.clear()
                self.matchStatus = "invalid"
                self.message = "Provider lyrics are missing the requested catalog ID."
                return
            if not self.engine.set_lyrics(result):
                self.engine.clear()
                self.matchStatus = "invalid"
                self.message = "Lyrics timeline is empty or invalid."
                return
            self.matchStatus = "matched"
            self.currentCatalogId = result.catalogId or (
                self.currentTrack.catalogId if self.currentTrack else None
            )
            self.message = None
        elif isinstance(result, Ambiguous):
            self.engine.clear()
            self.matchStatus = "ambiguous"
            self.message = result.message
        elif isinstance(result, InvalidMatch):
            self.engine.clear()
            self.matchStatus = "invalid"
            self.message = result.message
        elif isinstance(result, NotFound):
            self.engine.clear()
            self.matchStatus = "notFound"
            self.message = result.message
        else:
            self.engine.clear()
            self.matchStatus = "invalid"
            self.message = "Lyrics provider returned an unsupported result."


def track_identity(track: Optional[CurrentTrack]) -> Optional[Tuple[str, ...]]:
    if track is None:
        return None
    if track.catalogId:
        return (
            "catalog",
            track.catalogId,
            normalize_text(track.title),
            normalize_text(track.artist),
            normalize_text(track.album),
            "{:.3f}".format(float(track.duration)) if _finite_positive(track.duration) else "",
        )
    return (
        "metadata",
        normalize_text(track.title),
        normalize_text(track.artist),
        normalize_text(track.album),
        "{:.3f}".format(float(track.duration)) if _finite_positive(track.duration) else "",
    )


def _finite_positive(value: float) -> bool:
    try:
        number = float(value)
        return math.isfinite(number) and number > 0
    except (TypeError, ValueError):
        return False
