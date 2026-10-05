import json
import tempfile
import unittest
from dataclasses import replace
from concurrent.futures import Future
from pathlib import Path

import lyrics_simulate
from lyrics_provider import CurrentTrack, LyricLine, Lyrics, NotFound
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

    def test_zero_timestamp_shows_first_line(self):
        self.assertEqual("A1", self.engine.locate(0).current.text)

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


class CurrentTrackManagerTests(unittest.TestCase):
    def test_switch_track_clears_previous_lyrics_before_loading(self):
        provider = ProviderStub({
            "100000001": make_lyrics("A"),
            "100000002": make_lyrics("B"),
        })
        manager = CurrentTrackManager(provider)
        state_a = manager.update(snapshot(make_track()))
        self.assertEqual("A1", state_a.lyricPosition.current.text)
        state_b = manager.update(snapshot(make_track(
            title="B", catalog_id="100000002", position=0
        )))
        self.assertEqual("B1", state_b.lyricPosition.current.text)
        self.assertEqual(["100000001", "100000002"], provider.calls)

    def test_pause_and_resume_at_same_position_preserves_current_line(self):
        manager = CurrentTrackManager(ProviderStub({"100000001": make_lyrics()}))
        playing = manager.update(snapshot(make_track(position=0.5, state="playing")))
        paused = manager.update(snapshot(make_track(position=0.5, state="paused")))
        resumed = manager.update(snapshot(make_track(position=0.5, state="playing")))
        self.assertEqual("A1", playing.lyricPosition.current.text)
        self.assertEqual("A1", paused.lyricPosition.current.text)
        self.assertEqual("A1", resumed.lyricPosition.current.text)

    def test_unavailable_paused_position_does_not_show_a_lyric(self):
        manager = CurrentTrackManager(ProviderStub({"100000001": make_lyrics()}))
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
        manager = CurrentTrackManager(provider)
        manager.update(snapshot(make_track()))
        changed = manager.update(snapshot(make_track(catalog_id="100000002")))
        self.assertEqual("B1", changed.lyricPosition.current.text)
        self.assertEqual(["100000001", "100000002"], provider.calls)

    def test_duplicate_ticks_do_not_reload_same_track(self):
        provider = ProviderStub({"100000001": make_lyrics()})
        manager = CurrentTrackManager(provider)
        manager.update(snapshot(make_track()))
        manager.update(snapshot(make_track(position=0.2)))
        self.assertEqual(1, len(provider.calls))

    def test_no_lyrics_clears_engine(self):
        manager = CurrentTrackManager(ProviderStub({}))
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
        manager = CurrentTrackManager(provider)
        result = manager.update(snapshot(make_track()))
        self.assertEqual("invalid", result.matchStatus)
        self.assertIsNone(result.lyrics)

    def test_provider_result_catalog_id_must_match_current_track(self):
        provider = ProviderStub({"100000001": make_lyrics("B")})
        manager = CurrentTrackManager(provider)
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
