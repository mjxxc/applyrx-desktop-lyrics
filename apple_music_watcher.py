#!/usr/bin/env python3
"""Watch the Apple Music application lifecycle for the Applyrx desktop lyrics UI.

This module answers exactly one question: "is Apple Music running right now, and
did it just start or stop?" It deliberately knows nothing about lyrics, windows,
or the AppKit view layer. The single source of truth for playback state stays in
``main.get_player_info()`` and the lyrics engine is untouched.

Implementation notes
--------------------
``NSWorkspace`` delivers ``didLaunch``/``didTerminate`` notifications on the main
thread, but we defensively hop to the main thread anyway: every callback the
watcher invokes ends up touching NSWindow and menu state, and AppKit is not safe
to use from any other thread.

Startup order matters. Observers are registered *before* ``runningApplications``
is inspected, otherwise a launch that lands between the check and the
registration would be missed entirely.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

APPLE_MUSIC_BUNDLE_ID = "com.apple.Music"


def _load_appkit():
    """Import the AppKit pieces lazily so tests can stub them out.

    The notification names live on the AppKit *module*, not on NSWorkspace
    itself, and are spelled with the ``NS`` prefix.
    """
    from AppKit import (
        NSWorkspace,
        NSWorkspaceDidLaunchApplicationNotification,
        NSWorkspaceDidTerminateApplicationNotification,
    )

    return (
        NSWorkspace,
        NSWorkspaceDidLaunchApplicationNotification,
        NSWorkspaceDidTerminateApplicationNotification,
    )


class AppleMusicWatcher:
    """Emit ``on_music_launched`` / ``on_music_terminated`` for com.apple.Music.

    The watcher is intentionally idempotent: :meth:`start` on an already-started
    watcher is a no-op, so repeated AppDelegate setup cannot double-register
    observers or fan out duplicate callbacks.
    """

    def __init__(
        self,
        on_music_launched: Optional[Callable[[], None]] = None,
        on_music_terminated: Optional[Callable[[], None]] = None,
    ):
        self.on_music_launched = on_music_launched
        self.on_music_terminated = on_music_terminated
        self._started = False
        self._workspace = None
        self._launch_token = None
        self._terminate_token = None
        # Guards start/stop against concurrent callers (e.g. quit racing a launch
        # notification) without ever blocking the main thread.
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._started

    def start(self) -> None:
        """Register observers, then replay the current Music state exactly once."""
        with self._lock:
            if self._started:
                return

            workspace_class, launch_name, terminate_name = _load_appkit()
            self._workspace = workspace_class.sharedWorkspace()

            notification_center = self._workspace.notificationCenter()
            self._launch_token = notification_center.addObserverForName_object_queue_usingBlock_(
                launch_name,
                None,
                None,
                self._make_launch_block(),
            )
            self._terminate_token = notification_center.addObserverForName_object_queue_usingBlock_(
                terminate_name,
                None,
                None,
                self._make_terminate_block(),
            )
            self._started = True

        # Observers are live before we look, so a launch racing this check is
        # still delivered. The replay below is deduplicated by the AppDelegate.
        if self.is_music_running():
            self._dispatch(self.on_music_launched)

    def stop(self) -> None:
        """Remove observers. Safe to call when never started."""
        with self._lock:
            if not self._started:
                return
            _load_appkit()
            notification_center = self._workspace.notificationCenter()
            if self._launch_token is not None:
                notification_center.removeObserver_(self._launch_token)
            if self._terminate_token is not None:
                notification_center.removeObserver_(self._terminate_token)
            self._launch_token = None
            self._terminate_token = None
            self._workspace = None
            self._started = False

    def is_music_running(self) -> bool:
        """Return True when Apple Music appears in the running application list."""
        if self._workspace is None:
            workspace_class, _, _ = _load_appkit()
            self._workspace = workspace_class.sharedWorkspace()
        for application in self._workspace.runningApplications():
            if self._is_apple_music(application):
                return True
        return False

    # ── internals ────────────────────────────────────────────

    def _is_apple_music(self, application) -> bool:
        """Match on bundle identifier; fall back to localized name when absent."""
        if application is None:
            return False
        try:
            bundle_id = application.bundleIdentifier()
        except Exception:
            bundle_id = None
        if bundle_id == APPLE_MUSIC_BUNDLE_ID:
            return True
        if bundle_id:
            return False
        # bundleIdentifier() can be nil for odd processes; Music is the only
        # thing we care about, so a name check keeps the common path working.
        try:
            return application.localizedName() == "Music"
        except Exception:
            return False

    def _make_launch_block(self):
        def handler(notification) -> None:
            self._handle_app_launch(notification)

        return handler

    def _make_terminate_block(self):
        def handler(notification) -> None:
            self._handle_app_terminate(notification)

        return handler

    def _handle_app_launch(self, notification) -> None:
        application = self._application_from(notification)
        if self._is_apple_music(application):
            self._dispatch(self.on_music_launched)

    def _handle_app_terminate(self, notification) -> None:
        application = self._application_from(notification)
        if self._is_apple_music(application):
            self._dispatch(self.on_music_terminated)

    @staticmethod
    def _application_from(notification):
            if notification is None:
                return None
            try:
                # ``userInfo`` is an ObjC selector on NSNotification, so it must be
                # called; treating it as a mapping raises inside the notification
                # callback and would abort the app.
                user_info = notification.userInfo()
            except Exception:
                return None
            if user_info is None:
                return None
            try:
                return user_info.get("NSWorkspaceApplicationKey")
            except Exception:
                return None

    @staticmethod
    def _dispatch(callback: Optional[Callable[[], None]]) -> None:
        """Run a callback on the main thread; never raise into AppKit."""
        if callback is None:
            return

        def invoke() -> None:
            try:
                callback()
            except Exception:
                import traceback
                with open("/tmp/applyrx.log", "a", encoding="utf-8") as handle:
                    handle.write("watcher callback error:\n" + traceback.format_exc())

        try:
            from Foundation import NSOperationQueue

            NSOperationQueue.mainQueue().addOperationWithBlock_(invoke)
        except Exception:
            # No AppKit runtime (unit tests): fall back to a direct call.
            invoke()