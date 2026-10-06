import unittest
import tempfile
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from lyrics_panel_bridge import find_panel_executable, panel_message, presentation_state
from lyrics_provider import CurrentTrack, LyricLine, LyricWord, Lyrics
from lyrics_sync import (
    CurrentTrackManager,
    PlaybackSnapshot,
)


class ProviderStub:
    def __init__(self, result):
        self.result = result

    def get_lyrics(self, track):
        return self.result


class ImmediateExecutor:
    def submit(self, function, *args):
        future = Future()
        future.set_result(function(*args))
        return future

    def shutdown(self, **kwargs):
        pass


def make_snapshot(state="playing", position=0):
    track = CurrentTrack(
        title="A",
        artist="Artist",
        album="Album",
        duration=10,
        catalogId="100000001",
        playbackPosition=position,
        playbackState=state,
    )
    return PlaybackSnapshot(track, position, state, 1.0)


class PanelViewModelTests(unittest.TestCase):
    def test_matched_track_projects_engine_position_without_recalculating_index(self):
        lyrics = Lyrics(
            (LyricLine(0, 1, "first"), LyricLine(1, 2, "second")),
            "en",
            False,
            "100000001",
        )
        manager = CurrentTrackManager(
            ProviderStub(lyrics),
            executor=ImmediateExecutor(),
        )
        payload = presentation_state(manager.update(make_snapshot(position=1.5)))
        self.assertEqual("A", payload["title"])
        self.assertEqual("Artist", payload["artist"])
        self.assertEqual("matched", payload["matchStatus"])
        self.assertEqual("first", payload["previous"])
        self.assertEqual("second", payload["current"])
        self.assertNotIn("currentIndex", payload)
        self.assertNotIn("catalogId", payload)

    def test_paused_status_and_current_line_are_passed_through(self):
        lyrics = Lyrics((LyricLine(0, 2, "line"),), "en", False, "100000001")
        manager = CurrentTrackManager(
            ProviderStub(lyrics),
            executor=ImmediateExecutor(),
        )
        payload = presentation_state(manager.update(make_snapshot("paused", 0.5)))
        self.assertEqual("paused", payload["playbackState"])
        self.assertEqual("line", payload["current"])

    def test_current_lyric_serializes_word_timing_and_engine_position(self):
        lyrics = Lyrics(
            (LyricLine(
                0,
                2,
                "one two",
                (
                    LyricWord(0, 1, "one"),
                    LyricWord(1, 2, " two"),
                ),
            ),),
            "en",
            True,
            "100000001",
        )
        manager = CurrentTrackManager(ProviderStub(lyrics), executor=ImmediateExecutor())

        payload = presentation_state(manager.update(make_snapshot(position=1.25)))

        self.assertEqual(1.25, payload["playbackPosition"])
        self.assertEqual({
            "text": "one two",
            "startTime": 0,
            "endTime": 2,
            "words": [
                {"startTime": 0, "endTime": 1, "text": "one"},
                {"startTime": 1, "endTime": 2, "text": " two"},
            ],
        }, payload["currentLyric"])

    def test_projects_two_presentation_context_lines_without_relocating_current(self):
        lyrics = Lyrics(
            tuple(LyricLine(index, index + 1, f"line {index}") for index in range(5)),
            "en",
            False,
            "100000001",
        )
        manager = CurrentTrackManager(ProviderStub(lyrics), executor=ImmediateExecutor())

        payload = presentation_state(manager.update(make_snapshot(position=2.5)))

        self.assertEqual("line 2", payload["current"])
        self.assertEqual(["line 0", "line 1"], payload["previousLines"])
        self.assertEqual(["line 3", "line 4"], payload["nextLines"])

    def test_panel_message_carries_visibility_and_settings_commands(self):
        state = CurrentTrackManager(
            ProviderStub(None),
            executor=ImmediateExecutor(),
        ).update(make_snapshot())

        payload = panel_message(state, panel_visible=False, open_settings=True)

        self.assertFalse(payload["panelVisible"])
        self.assertTrue(payload["openSettings"])
        self.assertNotIn("panelVisible", panel_message(state))

    def test_missing_word_timing_keeps_plain_current_line(self):
        lyrics = Lyrics((LyricLine(0, 2, "plain line"),), "en", False, "100000001")
        manager = CurrentTrackManager(ProviderStub(lyrics), executor=ImmediateExecutor())

        payload = presentation_state(manager.update(make_snapshot(position=0.5)))

        self.assertEqual("plain line", payload["current"])
        self.assertEqual("plain line", payload["currentLyric"]["text"])
        self.assertEqual([], payload["currentLyric"]["words"])

    def test_invalid_word_timing_is_omitted_without_losing_line_text(self):
        from lyrics_sync import LyricsPosition, ManagerState

        snapshot = make_snapshot(position=0.5)
        invalid_words = (
            (LyricWord(0, 3, "out of bounds"),),
            (LyricWord(float("nan"), 1, "non-finite"),),
            (LyricWord("bad", 1, "wrong type"),),
        )
        for words in invalid_words:
            with self.subTest(words=words):
                line = LyricLine(0, 2, "safe fallback", words)
                state = ManagerState(
                    snapshot=snapshot,
                    lyrics=None,
                    lyricPosition=LyricsPosition(0.5, 0, line, None, None),
                    matchStatus="matched",
                    message=None,
                )

                payload = presentation_state(state)

                self.assertEqual("safe fallback", payload["current"])
                self.assertEqual("safe fallback", payload["currentLyric"]["text"])
                self.assertEqual([], payload["currentLyric"]["words"])

    def test_seek_pause_and_resume_positions_are_passed_without_local_clock(self):
        lyrics = Lyrics(
            (LyricLine(
                0,
                3,
                "one two three",
                (
                    LyricWord(0, 1, "one"),
                    LyricWord(1, 2, " two"),
                    LyricWord(2, 3, " three"),
                ),
            ),),
            "en",
            True,
            "100000001",
        )
        manager = CurrentTrackManager(ProviderStub(lyrics), executor=ImmediateExecutor())

        initial = presentation_state(manager.update(make_snapshot(position=0.25)))
        seeked = presentation_state(manager.update(make_snapshot(position=2.25)))
        paused = presentation_state(manager.update(make_snapshot("paused", 2.25)))
        resumed = presentation_state(manager.update(make_snapshot(position=2.75)))

        self.assertEqual(0.25, initial["playbackPosition"])
        self.assertEqual(2.25, seeked["playbackPosition"])
        self.assertEqual(2.25, paused["playbackPosition"])
        self.assertEqual(2.75, resumed["playbackPosition"])
        self.assertEqual("three", resumed["currentLyric"]["words"][2]["text"].strip())

    def test_loading_and_no_lyrics_have_explicit_view_states(self):
        track = make_snapshot().track
        pending_executor = type("PendingExecutor", (), {
            "submit": lambda *_: type("PendingFuture", (), {
                "done": lambda *_: False,
                "cancel": lambda *_: True,
            })()
        })()
        loading = CurrentTrackManager(ProviderStub(None), executor=pending_executor)
        pending = presentation_state(loading.update(make_snapshot()))
        self.assertEqual("loading", pending["matchStatus"])
        self.assertIsNone(pending["current"])

        from lyrics_provider import NotFound
        missing = CurrentTrackManager(
            ProviderStub(NotFound("not cached")),
            executor=ImmediateExecutor(),
        )
        absent = presentation_state(missing.update(make_snapshot()))
        self.assertEqual("notFound", absent["matchStatus"])
        self.assertIsNone(absent["current"])

    def test_track_switch_projects_new_track_without_old_lyrics(self):
        first = Lyrics(
            (LyricLine(0, 2, "old", (LyricWord(0, 2, "old"),)),),
            "en",
            True,
            "100000001",
        )

        class SwitchingProvider:
            def get_lyrics(self, track):
                if track.catalogId == "100000001":
                    return first
                from lyrics_provider import NotFound
                return NotFound("not cached")

        manager = CurrentTrackManager(
            SwitchingProvider(),
            executor=ImmediateExecutor(),
        )
        manager.update(make_snapshot(position=0.5))
        second_track = CurrentTrack(
            "B", "Artist B", "Album B", 12, "100000002", 0.5, "playing"
        )
        changed = PlaybackSnapshot(second_track, 0.5, "playing", 2.0)
        payload = presentation_state(manager.update(changed))
        self.assertEqual("B", payload["title"])
        self.assertEqual("notFound", payload["matchStatus"])
        self.assertIsNone(payload["current"])
        self.assertIsNone(payload["currentLyric"])
        self.assertIsNone(payload["previous"])

    def test_native_executable_lookup_accepts_explicit_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "ApplyrxLyricsPanel"
            executable.touch()
            with patch("lyrics_panel_bridge.os.access", return_value=True):
                self.assertEqual(executable, find_panel_executable(executable))


if __name__ == "__main__":
    unittest.main()
