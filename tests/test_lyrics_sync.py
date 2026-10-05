import json
import threading
import tempfile
import unittest
from dataclasses import replace
from concurrent.futures import Future
from pathlib import Path

import lyrics_simulate
from lyrics_provider import (
    AppleMusicCacheProvider,
    CacheResponse,
    CurrentTrack,
    LyricLine,
    Lyrics,
    NotFound,
)
from lyrics_sync import (
    CurrentTrackManager,
    LyricsEngine,
    NowPlayingWatcher,
    PlaybackSnapshot,
)


def make_track(
    title="A",
    catalog_id="100000001",
    artist="Artist",
    album="Album",
    duration=10,
    position=0.0,
    state="playing",
):
    return CurrentTrack(
        title=title,
        artist=artist,
        album=album,
        duration=duration,
        catalogId=catalog_id,
        playbackPosition=position,
        playbackState=state,
    )


def make_lyrics(prefix="A", catalog_id=None):
    return Lyrics(
        lines=(
            LyricLine(0.0, 1.0, prefix + "1"),
            LyricLine(2.0, 3.0, prefix + "2"),
        ),
        language="en",
        hasWordTiming=False,
        catalogId=catalog_id or ("100000002" if prefix == "B" else "100000001"),
    )


def snapshot(track, position=None, state=None, at=0.0):
    return PlaybackSnapshot(
        track=track,
        position=track.playbackPosition if position is None else position,
        playbackState=state or track.playbackState or "playing",
        sampledAt=at,
    )


class LyricsEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = LyricsEngine(make_lyrics())

    @staticmethod
    def overlap_engine():
        return LyricsEngine(Lyrics((
            LyricLine(0.0, 3.0, "first"),
            LyricLine(2.8, 6.0, "second"),
        ), "en", False))

    def test_zero_timestamp_shows_first_line(self):
        self.assertEqual("A1", self.engine.locate(0).current.text)

    def test_normal_non_overlapping_timeline_remains_valid(self):
        engine = LyricsEngine(Lyrics((
            LyricLine(0.0, 3.0, "first"),
            LyricLine(3.0, 6.0, "second"),
        ), "en", False))

        self.assertTrue(engine.valid)
        self.assertEqual("first", engine.locate(2.5).current.text)
        self.assertEqual("second", engine.locate(3.0).current.text)

    def test_overlap_is_valid_and_preserves_original_intervals(self):
        engine = self.overlap_engine()

        self.assertTrue(engine.valid)
        self.assertEqual(3.0, engine.lyrics.lines[0].endTime)
        self.assertEqual(2.8, engine.lyrics.lines[1].startTime)

    def test_overlap_selects_latest_started_line_and_neighbors(self):
        engine = LyricsEngine(Lyrics((
            LyricLine(0.0, 3.0, "first"),
            LyricLine(2.8, 6.0, "second"),
            LyricLine(6.5, 8.0, "third"),
        ), "en", False))

        first = engine.locate(2.5)
        self.assertEqual(0, first.currentIndex)
        self.assertEqual("first", first.current.text)
        self.assertIsNone(first.previous)
        self.assertEqual("second", first.next.text)

        overlap = engine.locate(2.9)
        self.assertEqual(1, overlap.currentIndex)
        self.assertEqual("first", overlap.previous.text)
        self.assertEqual("second", overlap.current.text)
        self.assertEqual("third", overlap.next.text)

    def test_overlap_continues_until_selected_line_end_then_becomes_gap(self):
        engine = self.overlap_engine()

        self.assertEqual("second", engine.locate(5.9).current.text)
        after_end = engine.locate(6.1)
        self.assertIsNone(after_end.current)
        self.assertIsNone(after_end.currentIndex)
        self.assertEqual("second", after_end.previous.text)
        self.assertIsNone(after_end.next)

    def test_gap_between_lines_remains_without_current(self):
        engine = LyricsEngine(Lyrics((
            LyricLine(0.0, 3.0, "first"),
            LyricLine(5.0, 8.0, "second"),
        ), "en", False))

        result = engine.locate(4.0)

        self.assertTrue(engine.valid)
        self.assertIsNone(result.current)
        self.assertIsNone(result.currentIndex)
        self.assertEqual("first", result.previous.text)
        self.assertEqual("second", result.next.text)

    def test_reverse_interval_is_invalid(self):
        engine = LyricsEngine(Lyrics((
            LyricLine(5.0, 3.0, "reversed"),
        ), "en", False))

        self.assertFalse(engine.valid)
        self.assertIsNone(engine.locate(4.0).current)

    def test_equal_start_times_are_invalid_ambiguous_order(self):
        engine = LyricsEngine(Lyrics((
            LyricLine(0.0, 3.0, "first"),
            LyricLine(0.0, 5.0, "same start"),
        ), "en", False))

        self.assertFalse(engine.valid)
        self.assertIsNone(engine.locate(1.0).current)

    def test_real_lantingxu_redacted_overlap_fixture_is_valid(self):
        fixture_path = Path(__file__).parent / "fixtures" / "lantingxu_overlap_timing.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        engine = LyricsEngine(Lyrics(tuple(
            LyricLine(item["start"], item["end"], "redacted")
            for item in fixture["lines"]
        ), "zh-Hans", False))

        self.assertTrue(engine.valid)
        overlap = engine.locate(127.1)
        self.assertEqual(1, overlap.currentIndex)
        self.assertEqual(127.010, overlap.current.startTime)
        self.assertEqual(132.903, overlap.current.endTime)

    def test_gap_has_no_current_with_previous_and_next(self):
        result = self.engine.locate(1.5)
        self.assertIsNone(result.current)
        self.assertEqual("A1", result.previous.text)
        self.assertEqual("A2", result.next.text)
        self.assertIsNone(result.currentIndex)

    def test_transition_to_next_line_at_its_start(self):
        self.assertEqual("A2", self.engine.locate(2.0).current.text)

    def test_paused_position_remains_pure(self):
        first = self.engine.locate(0.5)
        second = self.engine.locate(0.5)
        self.assertEqual(first, second)
        self.assertEqual("A1", second.current.text)

    def test_seek_forward_recomputes_from_timestamp(self):
        self.assertEqual("A1", self.engine.locate(0.5).current.text)
        self.assertEqual("A2", self.engine.locate(2.5).current.text)

    def test_seek_backward_recomputes_from_timestamp(self):
        self.assertEqual("A2", self.engine.locate(2.5).current.text)
        self.assertEqual("A1", self.engine.locate(0.5).current.text)

    def test_song_end_has_no_current_or_next(self):
        result = self.engine.locate(10.0)
        self.assertIsNone(result.current)
        self.assertEqual("A2", result.previous.text)
        self.assertIsNone(result.next)

    def test_empty_timeline_is_safe(self):
        engine = LyricsEngine(Lyrics((), "en", False))
        result = engine.locate(0)
        self.assertIsNone(result.current)
        self.assertIsNone(result.previous)
        self.assertIsNone(result.next)
        self.assertIsNone(result.currentIndex)

    def test_non_monotonic_timeline_is_rejected(self):
        engine = LyricsEngine(Lyrics((
            LyricLine(2, 3, "late"),
            LyricLine(1, 2, "early"),
        ), "en", False))
        result = engine.locate(2.5)
        self.assertIsNone(result.current)
        self.assertIsNone(result.currentIndex)

    def test_invalid_time_is_safe(self):
        self.assertIsNone(self.engine.locate(float("nan")).current)
        self.assertIsNone(self.engine.locate(-1).current)

    def test_non_numeric_timeline_is_rejected_safely(self):
        engine = LyricsEngine(Lyrics((
            LyricLine("not-a-time", 1, "invalid"),
        ), "en", False))
        result = engine.locate(0)
        self.assertIsNone(result.current)
        self.assertIsNone(result.currentIndex)


class WatcherTests(unittest.TestCase):
    def test_injected_reader_detects_track_state_and_seek_changes(self):
        ticks = iter([1.0, 2.0, 3.0, 4.0, 5.0])
        tracks = iter([
            make_track(position=0, state="playing"),
            make_track(position=1.0, state="playing"),
            make_track(position=1.0, state="paused"),
            make_track(position=7.0, state="paused"),
            make_track(title="B", catalog_id="100000002", position=0, state="playing"),
        ])
        watcher = NowPlayingWatcher(reader=lambda: next(tracks), clock=lambda: next(ticks))
        first = watcher.poll()
        second = watcher.poll()
        paused = watcher.poll()
        seeked = watcher.poll()
        changed = watcher.poll()
        self.assertFalse(first.trackChanged)
        self.assertTrue(second.positionChanged)
        self.assertTrue(paused.stateChanged)
        self.assertFalse(paused.seeked)
        self.assertTrue(seeked.seeked)
        self.assertTrue(changed.trackChanged)

    def test_paused_zero_position_holds_last_music_sample_without_elapsed_time(self):
        values = iter([
            make_track(position=8.5, state="playing"),
            make_track(position=0, state="paused"),
            make_track(position=0, state="paused"),
            make_track(position=9.25, state="playing"),
        ])
        watcher = NowPlayingWatcher(reader=lambda: next(values), clock=lambda: 1.0)
        playing = watcher.poll()
        paused = watcher.poll()
        still_paused = watcher.poll()
        resumed = watcher.poll()
        self.assertEqual(8.5, playing.position)
        self.assertEqual(8.5, paused.position)
        self.assertTrue(paused.positionHeld)
        self.assertEqual(8.5, still_paused.position)
        self.assertTrue(still_paused.positionHeld)
        self.assertEqual(9.25, resumed.position)
        self.assertFalse(resumed.positionHeld)

    def test_watcher_started_paused_with_zero_position_marks_position_unavailable(self):
        watcher = NowPlayingWatcher(
            reader=lambda: make_track(position=0, state="paused"),
            clock=lambda: 1.0,
        )
        sample = watcher.poll()
        self.assertFalse(sample.positionAvailable)


class ProviderStub:
    def __init__(self, result_by_id):
        self.result_by_id = result_by_id
        self.calls = []

    def get_lyrics(self, track):
        self.calls.append(track.catalogId)
        return self.result_by_id.get(track.catalogId, NotFound("not cached"))


class ImmediateExecutor:
    """Run quick test providers at submission time while exercising Future resolution."""

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, **kwargs):
        pass


class FakeClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class QueuedExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, function, *args):
        class RunningFuture(Future):
            cancel_attempted = False

            def cancel(self):
                self.cancel_attempted = True
                return False

        future = RunningFuture()
        self.calls.append((future, function, args))
        return future

    def run(self, index):
        future, function, args = self.calls[index]
        try:
            future.set_result(function(*args))
        except Exception as exc:
            future.set_exception(exc)

    def shutdown(self, **kwargs):
        pass


def immediate_manager(provider):
    return CurrentTrackManager(provider, executor=ImmediateExecutor())


class CurrentTrackManagerTests(unittest.TestCase):
    def test_provider_match_with_overlapping_timeline_is_manager_matched(self):
        overlapping = Lyrics(
            lines=(
                LyricLine(125.507, 127.370, "first"),
                LyricLine(127.010, 132.903, "second"),
            ),
            language="zh-Hans",
            hasWordTiming=False,
            catalogId="100000001",
        )
        manager = immediate_manager(ProviderStub({"100000001": overlapping}))

        state = manager.update(snapshot(make_track(position=127.1)))

        self.assertEqual("matched", state.matchStatus)
        self.assertEqual(1, state.lyricPosition.currentIndex)
        self.assertEqual("second", state.lyricPosition.current.text)

    def test_switch_track_clears_previous_lyrics_before_loading(self):
        provider = ProviderStub({
            "100000001": make_lyrics("A"),
            "100000002": make_lyrics("B"),
        })
        manager = immediate_manager(provider)
        state_a = manager.update(snapshot(make_track()))
        self.assertEqual("A1", state_a.lyricPosition.current.text)
        state_b = manager.update(snapshot(make_track(
            title="B", catalog_id="100000002", position=0
        )))
        self.assertEqual("B1", state_b.lyricPosition.current.text)
        self.assertEqual(["100000001", "100000002"], provider.calls)

    def test_pause_and_resume_at_same_position_preserves_current_line(self):
        manager = immediate_manager(ProviderStub({"100000001": make_lyrics()}))
        playing = manager.update(snapshot(make_track(position=0.5, state="playing")))
        paused = manager.update(snapshot(make_track(position=0.5, state="paused")))
        resumed = manager.update(snapshot(make_track(position=0.5, state="playing")))
        self.assertEqual("A1", playing.lyricPosition.current.text)
        self.assertEqual("A1", paused.lyricPosition.current.text)
        self.assertEqual("A1", resumed.lyricPosition.current.text)

    def test_unavailable_paused_position_does_not_show_a_lyric(self):
        manager = immediate_manager(ProviderStub({"100000001": make_lyrics()}))
        paused = snapshot(make_track(position=0, state="paused"))
        paused = replace(paused, positionAvailable=False)
        state = manager.update(paused)
        self.assertIsNone(state.lyricPosition.current)
        self.assertIsNone(state.lyricPosition.currentIndex)

    def test_catalog_id_change_clears_old_engine_even_same_title(self):
        provider = ProviderStub({
            "100000001": make_lyrics("A"),
            "100000002": make_lyrics("B"),
        })
        manager = immediate_manager(provider)
        manager.update(snapshot(make_track()))
        changed = manager.update(snapshot(make_track(catalog_id="100000002")))
        self.assertEqual("B1", changed.lyricPosition.current.text)
        self.assertEqual(["100000001", "100000002"], provider.calls)

    def test_duplicate_ticks_do_not_reload_same_track(self):
        provider = ProviderStub({"100000001": make_lyrics()})
        manager = immediate_manager(provider)
        manager.update(snapshot(make_track()))
        manager.update(snapshot(make_track(position=0.2)))
        self.assertEqual(1, len(provider.calls))

    def test_no_lyrics_clears_engine(self):
        manager = immediate_manager(ProviderStub({}))
        result = manager.update(snapshot(make_track()))
        self.assertEqual("notFound", result.matchStatus)
        self.assertIsNone(result.lyrics)
        self.assertIsNone(result.lyricPosition.current)

    def test_delayed_old_track_result_cannot_replace_new_track(self):
        class QueuedExecutor:
            def __init__(self):
                self.futures = []

            def submit(self, function, *args):
                class RunningFuture(Future):
                    def cancel(self):
                        return False

                future = RunningFuture()
                self.futures.append((future, args[0]))
                return future

        executor = QueuedExecutor()
        provider = ProviderStub({})
        manager = CurrentTrackManager(provider, executor=executor)
        manager.update(snapshot(make_track()))
        manager.update(snapshot(make_track(
            title="B", catalog_id="100000002", position=0
        )))
        self.assertIsNone(manager.engine.lyrics)
        executor.futures[0][0].set_result(make_lyrics("A"))
        state = manager.update(snapshot(make_track(
            title="B", catalog_id="100000002", position=0
        )))
        self.assertIsNone(state.lyrics)
        self.assertEqual("loading", state.matchStatus)
        executor.futures[1][0].set_result(make_lyrics("B"))
        state = manager.update(snapshot(make_track(
            title="B", catalog_id="100000002", position=0
        )))
        self.assertEqual("B1", state.lyricPosition.current.text)

    def test_invalid_provider_timeline_is_never_accepted(self):
        provider = ProviderStub({
            "100000001": Lyrics((
                LyricLine(2, 3, "later"),
                LyricLine(1, 2, "earlier"),
            ), "en", False, "100000001"),
        })
        manager = immediate_manager(provider)
        result = manager.update(snapshot(make_track()))
        self.assertEqual("invalid", result.matchStatus)
        self.assertIsNone(result.lyrics)

    def test_provider_result_catalog_id_must_match_current_track(self):
        provider = ProviderStub({"100000001": make_lyrics("B")})
        manager = immediate_manager(provider)
        state = manager.update(snapshot(make_track()))
        self.assertEqual("invalid", state.matchStatus)
        self.assertIsNone(state.lyrics)

    def test_provider_timeout_does_not_fall_back_to_stale_lyrics(self):
        class FakeClock:
            value = 0.0

            def __call__(self):
                return self.value

        class QueuedExecutor:
            def submit(self, function, *args):
                return Future()

        clock = FakeClock()
        manager = CurrentTrackManager(
            ProviderStub({}),
            executor=QueuedExecutor(),
            clock=clock,
            provider_timeout=1.0,
        )
        state = manager.update(snapshot(make_track()))
        self.assertEqual("loading", state.matchStatus)
        clock.value = 2.0
        state = manager.update(snapshot(make_track(position=0.5)))
        self.assertEqual("timeout", state.matchStatus)
        self.assertIsNone(state.lyrics)

    def test_not_found_retries_same_track_after_half_second(self):
        clock = FakeClock()
        executor = QueuedExecutor()
        manager = CurrentTrackManager(ProviderStub({}), executor=executor, clock=clock)
        manager.update(snapshot(make_track()))
        self.assertEqual(1, len(executor.calls))

        executor.calls[0][0].set_result(NotFound("cache not ready"))
        state = manager.update(snapshot(make_track()))
        self.assertEqual("notFound", state.matchStatus)
        self.assertEqual(0.5, manager._next_retry_at - clock())
        manager.update(snapshot(make_track(position=0.2)))
        self.assertEqual(1, len(executor.calls))

        clock.advance(0.5)
        state = manager.update(snapshot(make_track(position=0.3)))
        self.assertEqual("loading", state.matchStatus)
        self.assertEqual(2, len(executor.calls))

    def test_second_retry_can_match_and_success_stops_retrying(self):
        clock = FakeClock()
        executor = QueuedExecutor()
        manager = CurrentTrackManager(ProviderStub({}), executor=executor, clock=clock)
        manager.update(snapshot(make_track()))
        executor.calls[0][0].set_result(NotFound("cache not ready"))
        manager.update(snapshot(make_track()))
        clock.advance(0.5)
        manager.update(snapshot(make_track(position=0.2)))
        executor.calls[1][0].set_result(make_lyrics())
        state = manager.update(snapshot(make_track(position=0.2)))
        self.assertEqual("matched", state.matchStatus)
        self.assertEqual("A1", state.lyricPosition.current.text)
        self.assertIsNone(manager._next_retry_at)

        clock.advance(120)
        state = manager.update(snapshot(make_track(position=0.4)))
        self.assertEqual("matched", state.matchStatus)
        self.assertEqual(2, len(executor.calls))

    def test_continuous_not_found_uses_requested_backoff_sequence(self):
        clock = FakeClock()
        executor = QueuedExecutor()
        manager = CurrentTrackManager(ProviderStub({}), executor=executor, clock=clock)
        manager.update(snapshot(make_track()))
        expected_delays = [0.5, 1, 2, 4, 8, 15, 30, 30]

        for index, delay in enumerate(expected_delays):
            executor.calls[index][0].set_result(NotFound("still absent"))
            manager.update(snapshot(make_track()))
            self.assertAlmostEqual(delay, manager._next_retry_at - clock())
            clock.advance(delay)
            manager.update(snapshot(make_track(position=clock())))
            self.assertEqual(index + 2, len(executor.calls))

    def test_track_switch_cancels_scheduled_and_in_flight_retry(self):
        clock = FakeClock()
        executor = QueuedExecutor()
        manager = CurrentTrackManager(ProviderStub({}), executor=executor, clock=clock)
        manager.update(snapshot(make_track()))
        executor.calls[0][0].set_result(NotFound("not cached"))
        manager.update(snapshot(make_track()))
        clock.advance(0.5)
        manager.update(snapshot(make_track(position=0.2)))

        state = manager.update(snapshot(make_track(
            title="B", catalog_id="100000002", position=0
        )))
        self.assertTrue(executor.calls[1][0].cancel_attempted)
        self.assertEqual("loading", state.matchStatus)
        self.assertEqual(3, len(executor.calls))
        self.assertIsNone(state.lyrics)

    def test_track_switch_cancels_retry_that_has_not_started(self):
        clock = FakeClock()
        executor = QueuedExecutor()
        manager = CurrentTrackManager(ProviderStub({}), executor=executor, clock=clock)
        manager.update(snapshot(make_track()))
        executor.calls[0][0].set_result(NotFound("A cache not ready"))
        manager.update(snapshot(make_track()))
        self.assertIsNotNone(manager._next_retry_at)

        state = manager.update(snapshot(make_track(
            title="B", catalog_id="100000002", position=0
        )))
        self.assertEqual("loading", state.matchStatus)
        self.assertIsNone(manager._next_retry_at)
        self.assertEqual(2, len(executor.calls))

    def test_old_track_retry_result_cannot_replace_new_track_result(self):
        clock = FakeClock()
        executor = QueuedExecutor()
        manager = CurrentTrackManager(ProviderStub({}), executor=executor, clock=clock)
        manager.update(snapshot(make_track()))
        executor.calls[0][0].set_result(NotFound("A cache not ready"))
        manager.update(snapshot(make_track()))
        clock.advance(0.5)
        manager.update(snapshot(make_track(position=0.2)))
        old_retry = executor.calls[1][0]

        manager.update(snapshot(make_track(title="B", catalog_id="100000002")))
        old_retry.set_result(make_lyrics("A"))
        state = manager.update(snapshot(make_track(
            title="B", catalog_id="100000002", position=0
        )))
        self.assertEqual("loading", state.matchStatus)
        self.assertIsNone(state.lyrics)

        executor.calls[2][0].set_result(make_lyrics("B"))
        state = manager.update(snapshot(make_track(
            title="B", catalog_id="100000002", position=0
        )))
        self.assertEqual("matched", state.matchStatus)
        self.assertEqual("B1", state.lyricPosition.current.text)

    def test_ambiguous_result_stops_automatic_retry(self):
        from lyrics_provider import Ambiguous

        clock = FakeClock()
        executor = QueuedExecutor()
        manager = CurrentTrackManager(ProviderStub({}), executor=executor, clock=clock)
        manager.update(snapshot(make_track()))
        executor.calls[0][0].set_result(Ambiguous("multiple cached songs"))
        state = manager.update(snapshot(make_track()))
        self.assertEqual("ambiguous", state.matchStatus)
        clock.advance(120)
        manager.update(snapshot(make_track(position=0.5)))
        self.assertEqual(1, len(executor.calls))
        self.assertIsNone(manager._next_retry_at)

    def test_invalid_result_stops_automatic_retry(self):
        from lyrics_provider import InvalidMatch

        clock = FakeClock()
        executor = QueuedExecutor()
        manager = CurrentTrackManager(ProviderStub({}), executor=executor, clock=clock)
        manager.update(snapshot(make_track()))
        executor.calls[0][0].set_result(InvalidMatch("conflicting metadata"))
        state = manager.update(snapshot(make_track()))
        self.assertEqual("invalid", state.matchStatus)
        clock.advance(120)
        manager.update(snapshot(make_track(position=0.5)))
        self.assertEqual(1, len(executor.calls))
        self.assertIsNone(manager._next_retry_at)

    def test_not_found_then_cache_appears_is_observed_on_next_retry(self):
        class AppearingLocalCache:
            ready = False

            def scan(self):
                if not self.ready:
                    return []
                ttml = (
                    "<tt xmlns='http://www.w3.org/ns/ttml'>"
                    "<body><div><p begin='0s' end='1s'>cached lyric</p>"
                    "</div></body></tt>"
                )
                song = {
                    "id": "100000001",
                    "type": "songs",
                    "attributes": {
                        "name": "A",
                        "artistName": "Artist",
                        "albumName": "Album",
                        "durationInMillis": 10000,
                    },
                    "relationships": {
                        "syllable-lyrics": {
                            "data": [{
                                "type": "syllable-lyrics",
                                "attributes": {
                                    "playParams": {"catalogId": "100000001"},
                                    "ttmlLocalizations": {"en": ttml},
                                },
                            }]
                        }
                    },
                }
                return [CacheResponse(
                    endpoint="/v1/catalog/cn/songs",
                    storefront="cn",
                    parameterNames=("extend[syllable-lyrics]",),
                    requestedSongIds=frozenset(),
                    payload={"data": [song]},
                )]

        clock = FakeClock()
        executor = QueuedExecutor()
        local_cache = AppearingLocalCache()
        provider = AppleMusicCacheProvider(scanner=local_cache)
        manager = CurrentTrackManager(provider, executor=executor, clock=clock)
        state = manager.update(snapshot(make_track()))
        self.assertEqual("loading", state.matchStatus)
        executor.run(0)
        state = manager.update(snapshot(make_track()))
        self.assertEqual("notFound", state.matchStatus)

        local_cache.ready = True
        clock.advance(0.5)
        state = manager.update(snapshot(make_track(position=0.2)))
        self.assertEqual("loading", state.matchStatus)
        executor.run(1)
        state = manager.update(snapshot(make_track(position=0.2)))
        self.assertEqual("matched", state.matchStatus)
        self.assertEqual("cached lyric", state.lyricPosition.current.text)

    def test_default_provider_execution_is_async_and_does_not_block_update(self):
        started = threading.Event()
        release = threading.Event()

        class SlowProvider:
            def get_lyrics(self, track):
                started.set()
                release.wait(timeout=2)
                return make_lyrics()

        manager = CurrentTrackManager(SlowProvider())
        try:
            state = manager.update(snapshot(make_track()))
            self.assertEqual("loading", state.matchStatus)
            self.assertTrue(started.wait(timeout=1))
        finally:
            release.set()
            manager.close()

    def test_new_track_query_starts_while_old_track_provider_is_still_running(self):
        a_started = threading.Event()
        b_started = threading.Event()
        release_a = threading.Event()

        class SlowSwitchProvider:
            def get_lyrics(self, track):
                if track.catalogId == "100000001":
                    a_started.set()
                    release_a.wait(timeout=2)
                    return make_lyrics("A")
                b_started.set()
                return make_lyrics("B")

        manager = CurrentTrackManager(SlowSwitchProvider())
        try:
            state_a = manager.update(snapshot(make_track()))
            self.assertEqual("loading", state_a.matchStatus)
            self.assertTrue(a_started.wait(timeout=1))
            state_b = manager.update(snapshot(make_track(
                title="B", catalog_id="100000002", position=0
            )))
            self.assertIn(state_b.matchStatus, ("loading", "matched"))
            self.assertTrue(b_started.wait(timeout=1))
        finally:
            release_a.set()
            manager.close()

    def test_retry_logs_requests_results_delays_and_track_cancellation(self):
        clock = FakeClock()
        executor = QueuedExecutor()
        manager = CurrentTrackManager(ProviderStub({}), executor=executor, clock=clock)
        with self.assertLogs("applyrx.lyrics", level="INFO") as captured:
            manager.update(snapshot(make_track()))
            executor.calls[0][0].set_result(NotFound("not cached"))
            manager.update(snapshot(make_track()))
            clock.advance(0.5)
            manager.update(snapshot(make_track(position=0.2)))
            executor.calls[1][0].set_result(NotFound("still not cached"))
            manager.update(snapshot(make_track(position=0.2)))
            manager.update(snapshot(make_track(title="B", catalog_id="100000002")))
        output = "\n".join(captured.output)
        self.assertIn("[LYRICS] request", output)
        self.assertIn("[LYRICS] notFound", output)
        self.assertIn("[LYRICS] retry in 0.5s", output)
        self.assertIn("[LYRICS] retry cancelled: track changed", output)


class SimulationCliTests(unittest.TestCase):
    def test_simulation_reads_json_times_and_prints_previous_current_next(self):
        fixture = {
            "lines": [
                {"start": 0, "end": 1, "text": "one"},
                {"start": 2, "end": 3, "text": "two"},
            ],
            "times": [0, 1.5, 2.5, 4],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sync.json"
            path.write_text(json.dumps(fixture), encoding="utf-8")
            from contextlib import redirect_stdout
            import io
            output = io.StringIO()
            with redirect_stdout(output):
                code = lyrics_simulate.main([str(path)])
        rows = output.getvalue().splitlines()
        self.assertEqual(0, code)
        self.assertIn("Timestamp=0.000 Previous=- Current=one Next=two", rows[0])
        self.assertIn("Timestamp=1.500 Previous=one Current=- Next=two", rows[1])
        self.assertIn("Timestamp=2.500 Previous=one Current=two Next=-", rows[2])
        self.assertIn("Timestamp=4.000 Previous=two Current=- Next=-", rows[3])


if __name__ == "__main__":
    unittest.main()
