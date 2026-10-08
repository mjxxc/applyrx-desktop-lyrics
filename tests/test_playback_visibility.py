"""Tests for playback-aware lyrics visibility (v0.2.2)."""

import unittest
from unittest.mock import MagicMock, patch

import applyrx_ui


def player(state="playing", running=True, name="Song", artist="Artist"):
    """Build a player snapshot shaped like ``main.get_player_info()``."""
    return {
        "running": running,
        "state": state,
        "name": name,
        "artist": artist,
        "position": 12.0,
        "duration": 200.0,
    }


class PlaybackVisibilityTests(unittest.TestCase):
    """Drives AppDelegate._syncPlaybackVisibility on a real ObjC instance."""

    def setUp(self):
        applyrx_ui.CONFIG = dict(applyrx_ui.DEFAULT_CONFIG)
        applyrx_ui.CONFIG["desktop_visible"] = False
        self.delegate = applyrx_ui.AppDelegate.alloc().init()
        self.delegate.native_panel_bridge = None
        self.delegate.window = MagicMock()
        self.delegate.refreshMenu = MagicMock()
        self.delegate.desktop_hidden_manually = False
        self.delegate._resetPlaybackState()
        # Record every visibility change instead of touching AppKit. CONFIG is
        # updated too, because the visibility rule reads it to avoid repeating a
        # show/hide that has already been applied.
        self.calls = []

        def record(visible, manual=False):
            self.calls.append((bool(visible), bool(manual)))
            applyrx_ui.CONFIG["desktop_visible"] = bool(visible)

        self.delegate._setDesktopLyricsVisible = record

    def tearDown(self):
        applyrx_ui.CONFIG = dict(applyrx_ui.DEFAULT_CONFIG)

    # ── basic show / hide ─────────────────────────────────────

    def test_playing_shows(self):
        self.delegate._syncPlaybackVisibility(player("playing"))
        self.assertIn((True, False), self.calls)

    def test_paused_hides_after_debounce(self):
        applyrx_ui.CONFIG["auto_show_while_playing"] = True
        applyrx_ui.CONFIG["desktop_visible"] = True  # lyrics are up before pausing
        with patch.object(applyrx_ui, "PAUSE_HIDE_DEBOUNCE", 0.4), \
             patch("applyrx_ui.time.monotonic", side_effect=[10.0, 10.2, 10.7]):
            self.delegate._syncPlaybackVisibility(player("paused"))
            self.delegate._syncPlaybackVisibility(player("paused"))
            self.delegate._syncPlaybackVisibility(player("paused"))
        self.assertIn((False, False), self.calls)

    def test_paused_does_not_hide_before_debounce(self):
        applyrx_ui.CONFIG["auto_show_while_playing"] = True
        with patch.object(applyrx_ui, "PAUSE_HIDE_DEBOUNCE", 0.4), \
             patch("applyrx_ui.time.monotonic", side_effect=[10.0, 10.2]):
            self.delegate._syncPlaybackVisibility(player("paused"))
            self.delegate._syncPlaybackVisibility(player("paused"))
        self.assertNotIn((False, False), self.calls)

    def test_resume_shows_immediately(self):
        applyrx_ui.CONFIG["auto_show_while_playing"] = True
        applyrx_ui.CONFIG["desktop_visible"] = False  # hidden by the pause
        self.delegate._syncPlaybackVisibility(player("paused"))
        self.calls.clear()
        self.delegate._syncPlaybackVisibility(player("playing"))
        self.assertIn((True, False), self.calls)

    def test_brief_pause_does_not_flicker(self):
        """Pausing then resuming within the debounce window must not hide."""
        applyrx_ui.CONFIG["auto_show_while_playing"] = True
        with patch.object(applyrx_ui, "PAUSE_HIDE_DEBOUNCE", 0.4), \
             patch("applyrx_ui.time.monotonic", side_effect=[10.0, 10.1, 10.2]):
            self.delegate._syncPlaybackVisibility(player("paused"))
            self.delegate._syncPlaybackVisibility(player("playing"))
            self.delegate._syncPlaybackVisibility(player("paused"))
        self.assertNotIn((False, False), self.calls)

    def test_music_not_running_resets_and_hides_nothing(self):
        # The lifecycle watcher owns the "not running" case; the playback rule
        # only clears its debounce state so a later pause is not stale.
        self.delegate._syncPlaybackVisibility(player("paused"))
        self.delegate._syncPlaybackVisibility(player(None, running=False))
        self.assertIsNone(self.delegate._paused_since)
        self.assertIsNone(self.delegate._last_playback_state)

    def test_stopped_state_does_not_hide(self):
        applyrx_ui.CONFIG["auto_show_while_playing"] = True
        self.delegate._syncPlaybackVisibility(player("stopped"))
        self.assertNotIn((False, False), self.calls)

    def test_invalid_snapshot_is_ignored(self):
        self.delegate._syncPlaybackVisibility(None)
        self.delegate._syncPlaybackVisibility("nonsense")
        self.assertEqual(self.calls, [])

    # ── manual override ───────────────────────────────────────

    def test_manual_hidden_survives_playing(self):
        """A user hiding the panel must not be overruled by playing state."""
        self.delegate.desktop_hidden_manually = True
        self.delegate._syncPlaybackVisibility(player("playing"))
        self.assertNotIn((True, False), self.calls)

    def test_manual_hidden_then_shown_restores_auto(self):
        self.delegate.desktop_hidden_manually = True
        self.delegate._syncPlaybackVisibility(player("playing"))
        self.assertEqual(self.calls, [])
        # User toggles back on via the menu / hot key.
        self.delegate.desktop_hidden_manually = False
        self.delegate._syncPlaybackVisibility(player("playing"))
        self.assertIn((True, False), self.calls)

    def test_pause_hide_is_not_recorded_as_manual(self):
        applyrx_ui.CONFIG["auto_show_while_playing"] = True
        applyrx_ui.CONFIG["desktop_visible"] = True
        with patch.object(applyrx_ui, "PAUSE_HIDE_DEBOUNCE", 0.0), \
             patch("applyrx_ui.time.monotonic", side_effect=[10.0, 11.0]):
            self.delegate._syncPlaybackVisibility(player("paused"))
            self.delegate._syncPlaybackVisibility(player("paused"))
        self.assertIn((False, False), self.calls)
        # manual=False: an automatic hide must not look like a user choice.
        self.assertFalse(self.delegate.desktop_hidden_manually)

    # ── configuration switch ──────────────────────────────────

    def test_playback_rule_disabled_blocks_pause_hide(self):
        applyrx_ui.CONFIG["auto_show_while_playing"] = False
        with patch.object(applyrx_ui, "PAUSE_HIDE_DEBOUNCE", 0.0), \
             patch("applyrx_ui.time.monotonic", side_effect=[10.0, 11.0]):
            self.delegate._syncPlaybackVisibility(player("paused"))
            self.delegate._syncPlaybackVisibility(player("paused"))
        self.assertNotIn((False, False), self.calls)

    def test_playback_rule_disabled_still_shows_on_playing(self):
        applyrx_ui.CONFIG["auto_show_while_playing"] = False
        self.delegate._syncPlaybackVisibility(player("playing"))
        self.assertIn((True, False), self.calls)

    # ── track change must not hide ────────────────────────────

    def test_track_change_while_playing_keeps_visible(self):
        self.delegate._syncPlaybackVisibility(player("playing", name="A"))
        self.delegate._syncPlaybackVisibility(player("playing", name="B"))
        self.assertNotIn((False, False), self.calls)

    def test_lyrics_match_failure_does_not_hide(self):
        """Matching lives in tick_; visibility only reacts to playback state."""
        self.delegate._syncPlaybackVisibility(player("playing"))
        # tick_ sets an error when no lyrics are found; visibility is unaffected
        # because it never inspects that field.
        self.delegate.error = "没有找到和当前歌名/时长匹配的歌词缓存"
        self.delegate._syncPlaybackVisibility(player("playing"))
        self.assertNotIn((False, False), self.calls)


class StartupVisibilityTests(unittest.TestCase):
    """Music paused at launch must not show the panel (spec section 9)."""

    def setUp(self):
        applyrx_ui.CONFIG = dict(applyrx_ui.DEFAULT_CONFIG)
        applyrx_ui.CONFIG["desktop_visible"] = True

    def tearDown(self):
        applyrx_ui.CONFIG = dict(applyrx_ui.DEFAULT_CONFIG)

    def test_launch_while_paused_does_not_show(self):
        delegate = applyrx_ui.AppDelegate.alloc().init()
        calls = []
        delegate._setDesktopLyricsVisible = lambda v, manual=False: calls.append(bool(v))
        delegate._resetPlaybackState()
        # Launch path forces hidden first, then playback decides.
        applyrx_ui.CONFIG["desktop_visible"] = False
        delegate._syncPlaybackVisibility(player("paused"))
        self.assertNotIn(True, calls)

    def test_launch_while_playing_shows(self):
        delegate = applyrx_ui.AppDelegate.alloc().init()
        calls = []
        delegate._setDesktopLyricsVisible = lambda v, manual=False: calls.append(bool(v))
        delegate._resetPlaybackState()
        applyrx_ui.CONFIG["desktop_visible"] = False
        delegate._syncPlaybackVisibility(player("playing"))
        self.assertIn(True, calls)

    def test_launch_resets_manual_override(self):
        """The manual flag is per-process and must not survive a restart."""
        applyrx_ui.CONFIG["desktop_visible"] = False
        delegate = applyrx_ui.AppDelegate.alloc().init()
        self.assertFalse(delegate.desktop_hidden_manually)


class PlaybackPollWhileNativeTests(unittest.TestCase):
    """The native panel owns the window, so tick_ still has to track playback."""

    def setUp(self):
        applyrx_ui.CONFIG = dict(applyrx_ui.DEFAULT_CONFIG)
        applyrx_ui.CONFIG["desktop_visible"] = False
        self.delegate = applyrx_ui.AppDelegate.alloc().init()
        self.delegate.native_panel_bridge = None
        self.delegate.refreshMenu = MagicMock()
        self.delegate.desktop_hidden_manually = False
        self.delegate._resetPlaybackState()
        self.calls = []
        self.delegate._setDesktopLyricsVisible = (
            lambda visible, manual=False: self.calls.append((bool(visible), bool(manual)))
        )

    def test_poll_samples_player_state(self):
        with patch("applyrx_ui.core.get_player_info", return_value=player("playing")) as mocked:
            with patch("applyrx_ui.time.monotonic", side_effect=[100.0, 100.1]):
                # First call is throttled by the sync timestamp, so prime it.
                self.delegate._playback_sync_at = 0.0
                self.delegate._pollPlaybackWhileNativePanelRuns()
        mocked.assert_called()
        self.assertIn((True, False), self.calls)

    def test_poll_is_throttled(self):
        self.delegate._playback_sync_at = 100.0
        with patch("applyrx_ui.core.get_player_info") as mocked:
            with patch("applyrx_ui.time.monotonic", return_value=100.1):
                self.delegate._pollPlaybackWhileNativePanelRuns()
        mocked.assert_not_called()

    def test_poll_survives_reader_failure(self):
        self.delegate._playback_sync_at = 0.0
        with patch("applyrx_ui.core.get_player_info", side_effect=RuntimeError("boom")):
            with patch("applyrx_ui.time.monotonic", return_value=200.0):
                self.delegate._pollPlaybackWhileNativePanelRuns()
        self.assertEqual(self.calls, [])


class NativePanelStartupTests(unittest.TestCase):
    """The panel must start hidden on launch, and start atall.

    Regression guard: AppDelegate methods are published to the ObjC runtime,
    which rejects keyword arguments outright. Calling
    ``_startNativePanel(panel_visible=False)`` raises TypeError at runtime and
    kills the app during launch, which no unit test would otherwise catch.
    """

    def setUp(self):
        applyrx_ui.CONFIG = dict(applyrx_ui.DEFAULT_CONFIG)

    def tearDown(self):
        applyrx_ui.CONFIG = dict(applyrx_ui.DEFAULT_CONFIG)

    def test_start_native_panel_accepts_positional_visibility(self):
        delegate = applyrx_ui.AppDelegate.alloc().init()
        delegate.native_panel_bridge = None
        delegate.lyrics_window = None
        delegate.refreshMenu = MagicMock()
        # Must not raise: keyword arguments are invalid for this selector.
        delegate._startNativePanel(False)

    def test_start_native_panel_rejects_keyword(self):
        delegate = applyrx_ui.AppDelegate.alloc().init()
        delegate.native_panel_bridge = None
        delegate.lyrics_window = None
        delegate.refreshMenu = MagicMock()
        with self.assertRaises(TypeError):
            delegate._startNativePanel(panel_visible=False)


if __name__ == "__main__":
    unittest.main()