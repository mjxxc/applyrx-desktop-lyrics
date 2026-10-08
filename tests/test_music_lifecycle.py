"""Tests for Apple Music lifecycle detection in applyrx_ui."""

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── lightweight AppKit stubs ────────────────────────────────

class FakeNotification:
    """Mirrors NSNotification: ``userInfo`` is a selector that must be called."""

    def __init__(self, application):
        self._user_info = (
            {"NSWorkspaceApplicationKey": application} if application else {}
        )

    def userInfo(self):
        return self._user_info


class FakeApplication:
    def __init__(self, bundle_id=None, name=None):
        self._bundle_id = bundle_id
        self._name = name

    def bundleIdentifier(self):
        return self._bundle_id

    def localizedName(self):
        return self._name


class FakeWorkspace:
    """Stands in for NSWorkspace: records observers, replays notifications."""

    notification_names = {}

    def __init__(self, running=None):
        self.running = list(running or [])
        self.observers = {}
        self._counter = 0
        self.removed = []

    def runningApplications(self):
        return list(self.running)

    def notificationCenter(self):
        return self

    def addObserverForName_object_queue_usingBlock_(self, name, obj, queue, block):
        self._counter += 1
        token = f"token-{self._counter}"
        self.observers[name] = block
        return token

    def removeObserver_(self, token):
        self.removed.append(token)

    def emit(self, name, application):
        block = self.observers.get(name)
        if block is not None:
            block(FakeNotification(application))


def install_appkit_stub(workspace):
    """Register a fake AppKit module so the watcher imports cleanly.

    Mirrors the real API: the notification names are module-level constants
    spelled with the ``NS`` prefix, not attributes of the NSWorkspace class.
    """
    appkit = types.ModuleType("AppKit")
    workspace_class = type("NSWorkspace", (), {
        "sharedWorkspace": staticmethod(lambda: workspace),
    })
    appkit.NSWorkspace = workspace_class
    appkit.NSWorkspaceDidLaunchApplicationNotification = "launch"
    appkit.NSWorkspaceDidTerminateApplicationNotification = "terminate"
    sys.modules["AppKit"] = appkit
    return workspace_class


class AppleMusicWatcherTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("AppKit", None)

    def _watcher(self, workspace, launched=None, terminated=None):
        install_appkit_stub(workspace)
        from apple_music_watcher import AppleMusicWatcher

        watcher = AppleMusicWatcher(
            on_music_launched=launched if launched is not None else MagicMock(),
            on_music_terminated=terminated if terminated is not None else MagicMock(),
        )
        # PyObjC is installed here, so the real main queue exists but never runs
        # without an event loop. Force the synchronous path the watcher uses when
        # AppKit is unavailable, so assertions observe the callback directly.
        patcher = patch(
            "apple_music_watcher.AppleMusicWatcher._dispatch",
            side_effect=lambda callback: callback() if callback else None,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return watcher

    # ── identification ───────────────────────────────────────

    def test_identifies_apple_music_by_bundle_id(self):
        workspace = FakeWorkspace()
        watcher = self._watcher(workspace)
        self.assertTrue(watcher._is_apple_music(FakeApplication("com.apple.Music")))
        self.assertFalse(watcher._is_apple_music(FakeApplication("com.apple.Safari")))
        self.assertFalse(watcher._is_apple_music(None))

    def test_falls_back_to_name_when_bundle_id_missing(self):
        workspace = FakeWorkspace()
        watcher = self._watcher(workspace)
        self.assertTrue(watcher._is_apple_music(FakeApplication(None, "Music")))
        self.assertFalse(watcher._is_apple_music(FakeApplication(None, "Safari")))

    def test_bundled_app_wins_over_name_collision(self):
        workspace = FakeWorkspace()
        watcher = self._watcher(workspace)
        self.assertFalse(watcher._is_apple_music(FakeApplication("com.other.Music", "Music")))

    # ── startup ordering ─────────────────────────────────────

    def test_start_registers_observers_before_checking_running_apps(self):
        """The replay must not be able to race past registration."""
        workspace = FakeWorkspace(running=[FakeApplication("com.apple.Music")])
        order = []
        install_appkit_stub(workspace)
        import apple_music_watcher

        original_add = workspace.addObserverForName_object_queue_usingBlock_

        def traced_add(name, obj, queue, block):
            order.append("observe")
            return original_add(name, obj, queue, block)

        workspace.addObserverForName_object_queue_usingBlock_ = traced_add
        original_running = workspace.runningApplications

        def traced_running():
            order.append("check")
            return original_running()

        workspace.runningApplications = traced_running

        watcher = apple_music_watcher.AppleMusicWatcher(on_music_launched=MagicMock())
        patcher = patch(
            "apple_music_watcher.AppleMusicWatcher._dispatch",
            side_effect=lambda callback: callback() if callback else None,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        watcher.start()
        self.assertEqual(order[:2], ["observe", "observe"])
        self.assertIn("check", order)
        self.assertLess(order.index("observe"), order.index("check"))

    def test_start_replays_launch_when_music_already_running(self):
        workspace = FakeWorkspace(running=[FakeApplication("com.apple.Music")])
        launched = MagicMock()
        watcher = self._watcher(workspace, launched=launched)
        watcher.start()
        launched.assert_called_once_with()

    def test_start_does_not_replay_when_music_absent(self):
        workspace = FakeWorkspace(running=[FakeApplication("com.apple.Safari")])
        launched = MagicMock()
        watcher = self._watcher(workspace, launched=launched)
        watcher.start()
        launched.assert_not_called()

    # ── notifications ────────────────────────────────────────

    def test_launch_notification_fires_callback(self):
        workspace = FakeWorkspace()
        launched = MagicMock()
        watcher = self._watcher(workspace, launched=launched)
        watcher.start()
        workspace.emit("launch", FakeApplication("com.apple.Music"))
        launched.assert_called_once_with()

    def test_launch_notification_ignores_other_apps(self):
        workspace = FakeWorkspace()
        launched = MagicMock()
        watcher = self._watcher(workspace, launched=launched)
        watcher.start()
        workspace.emit("launch", FakeApplication("com.apple.Safari"))
        launched.assert_not_called()

    def test_terminate_notification_fires_callback(self):
        workspace = FakeWorkspace()
        terminated = MagicMock()
        watcher = self._watcher(workspace, terminated=terminated)
        watcher.start()
        workspace.emit("terminate", FakeApplication("com.apple.Music"))
        terminated.assert_called_once_with()

    def test_terminate_notification_ignores_other_apps(self):
        workspace = FakeWorkspace()
        terminated = MagicMock()
        watcher = self._watcher(workspace, terminated=terminated)
        watcher.start()
        workspace.emit("terminate", FakeApplication("com.apple.Safari"))
        terminated.assert_not_called()

    def test_notification_without_user_info_is_safe(self):
        workspace = FakeWorkspace()
        launched = MagicMock()
        watcher = self._watcher(workspace, launched=launched)
        watcher.start()
        block = workspace.observers["launch"]
        block(FakeNotification(None))
        launched.assert_not_called()

    def test_user_info_is_called_not_treated_as_mapping(self):
        """NSNotification.userInfo is a selector; reading it as a dict crashes.

        This is a regression guard: accessing ``notification.userInfo.get(...)``
        raises inside the AppKit callback and aborts the process.
        """
        workspace = FakeWorkspace()
        launched = MagicMock()
        watcher = self._watcher(workspace, launched=launched)
        watcher.start()
        block = workspace.observers["launch"]
        # Would raise AttributeError if the watcher treated userInfo as a mapping.
        block(FakeNotification(FakeApplication("com.apple.Music")))
        launched.assert_called_once_with()

    def test_application_from_tolerates_broken_notification(self):
        workspace = FakeWorkspace()
        watcher = self._watcher(workspace)
        self.assertIsNone(watcher._application_from(None))

        class Exploding:
            def userInfo(self):
                raise RuntimeError("bad notification")

        self.assertIsNone(watcher._application_from(Exploding()))

    # ── idempotency & teardown ───────────────────────────────

    def test_double_start_does_not_register_twice(self):
        workspace = FakeWorkspace()
        watcher = self._watcher(workspace)
        watcher.start()
        count_after_first = len(workspace.observers)
        watcher.start()
        self.assertEqual(len(workspace.observers), count_after_first)
        self.assertTrue(watcher.is_running)

    def test_double_start_does_not_duplicate_callbacks(self):
        workspace = FakeWorkspace(running=[FakeApplication("com.apple.Music")])
        launched = MagicMock()
        watcher = self._watcher(workspace, launched=launched)
        watcher.start()
        watcher.start()
        launched.assert_called_once_with()

    def test_stop_removes_observers_and_is_idempotent(self):
        workspace = FakeWorkspace()
        watcher = self._watcher(workspace)
        watcher.start()
        watcher.stop()
        self.assertEqual(len(workspace.removed), 2)
        self.assertFalse(watcher.is_running)
        watcher.stop()
        self.assertEqual(len(workspace.removed), 2)

    def test_stop_without_start_is_safe(self):
        workspace = FakeWorkspace()
        watcher = self._watcher(workspace)
        watcher.stop()
        self.assertFalse(watcher.is_running)

    def test_callback_errors_do_not_propagate(self):
        workspace = FakeWorkspace()

        def boom():
            raise RuntimeError("callback failure")

        watcher = self._watcher(workspace, launched=boom)
        watcher.start()
        # Reverting the dispatch patch proves the production ``_dispatch`` path
        # swallows callback errors instead of letting them reach AppKit.
        patch.stopall()
        workspace.emit("launch", FakeApplication("com.apple.Music"))

    def test_none_callbacks_are_allowed(self):
        workspace = FakeWorkspace()
        install_appkit_stub(workspace)
        from apple_music_watcher import AppleMusicWatcher

        watcher = AppleMusicWatcher()
        watcher.start()
        workspace.emit("launch", FakeApplication("com.apple.Music"))
        workspace.emit("terminate", FakeApplication("com.apple.Music"))
        watcher.stop()

    # ── runningApplications handling ─────────────────────────

    def test_is_music_running_detects_running_app(self):
        workspace = FakeWorkspace(running=[FakeApplication("com.apple.Music")])
        watcher = self._watcher(workspace)
        watcher.start()
        self.assertTrue(watcher.is_music_running())

    def test_is_music_running_false_when_absent(self):
        workspace = FakeWorkspace(running=[FakeApplication("com.apple.Safari")])
        watcher = self._watcher(workspace)
        watcher.start()
        self.assertFalse(watcher.is_music_running())


class PanelEventParsingTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("AppKit", None)

    def test_parses_visibility_event(self):
        from lyrics_panel_bridge import parse_panel_event

        event = parse_panel_event(b'{"event":"visibilityChanged","visible":false}')
        self.assertEqual(event["event"], "visibilityChanged")
        self.assertFalse(event["visible"])

    def test_ignores_blank_and_invalid_lines(self):
        from lyrics_panel_bridge import parse_panel_event

        self.assertIsNone(parse_panel_event(b""))
        self.assertIsNone(parse_panel_event(b"not json"))
        self.assertIsNone(parse_panel_event(b"[1,2,3]"))


class DesktopVisibilityStateTests(unittest.TestCase):
    """Drive the AppDelegate visibility state machine without AppKit."""

    def setUp(self):
        sys.modules.pop("AppKit", None)

    def _make_delegate(self):
        import applyrx_ui

        applyrx_ui.CONFIG = dict(applyrx_ui.DEFAULT_CONFIG)
        applyrx_ui.CONFIG["desktop_visible"] = False
        # AppDelegate is an ObjC class, so the state machine must be exercised on
        # a real instance rather than a stand-in object.
        delegate = applyrx_ui.AppDelegate.alloc().init()
        delegate.native_panel_bridge = None
        delegate.window = MagicMock()
        delegate.refreshMenu = MagicMock()
        delegate.desktop_hidden_manually = False
        return applyrx_ui, delegate

    def test_auto_show_ignored_when_user_manually_hid(self):
        applyrx_ui, delegate = self._make_delegate()
        applyrx_ui.CONFIG["desktop_visible"] = True
        delegate.desktop_hidden_manually = True
        delegate._onMusicLaunched()
        # State is unchanged; nothing was restarted or re-shown.
        self.assertTrue(applyrx_ui.CONFIG["desktop_visible"])

    def test_auto_hide_resets_manual_flag(self):
        applyrx_ui, delegate = self._make_delegate()
        applyrx_ui.CONFIG["desktop_visible"] = True
        delegate.desktop_hidden_manually = True
        delegate._onMusicTerminated()
        self.assertFalse(applyrx_ui.CONFIG["desktop_visible"])
        self.assertFalse(delegate.desktop_hidden_manually)

    def test_auto_show_disabled_config_blocks_show(self):
        applyrx_ui, delegate = self._make_delegate()
        applyrx_ui.CONFIG["auto_show_with_music"] = False
        delegate._onMusicLaunched()
        self.assertFalse(applyrx_ui.CONFIG["desktop_visible"])

    def test_auto_hide_disabled_config_blocks_hide(self):
        applyrx_ui, delegate = self._make_delegate()
        applyrx_ui.CONFIG["desktop_visible"] = True
        applyrx_ui.CONFIG["auto_hide_with_music"] = False
        delegate._onMusicTerminated()
        self.assertTrue(applyrx_ui.CONFIG["desktop_visible"])

    def test_manual_toggle_sets_and_clears_flag(self):
        applyrx_ui, delegate = self._make_delegate()
        applyrx_ui.CONFIG["desktop_visible"] = True
        delegate._setDesktopLyricsVisible(False, True)
        self.assertTrue(delegate.desktop_hidden_manually)
        delegate._setDesktopLyricsVisible(True, True)
        self.assertFalse(delegate.desktop_hidden_manually)

    def test_repeated_show_does_not_restart_panel(self):
        applyrx_ui, delegate = self._make_delegate()
        applyrx_ui.CONFIG["desktop_visible"] = True
        bridge = MagicMock()
        bridge.is_running = True
        delegate.native_panel_bridge = bridge
        delegate._setDesktopLyricsVisible(True, False)
        # Already visible: the bridge must not be touched, so no second panel.
        bridge.set_panel_visible.assert_not_called()
        bridge.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()