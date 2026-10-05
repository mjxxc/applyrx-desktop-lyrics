"""Bridge live lyrics-engine snapshots to the native SwiftUI panel."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from lyrics_provider import AppleMusicCacheProvider
from lyrics_sync import CurrentTrackManager, ManagerState, NowPlayingWatcher


PROJECT_DIR = Path(__file__).resolve().parent


def presentation_state(state: ManagerState) -> dict:
    track = state.snapshot.track
    position = state.lyricPosition
    return {
        "title": track.title if track else "",
        "artist": track.artist if track else "",
        "playbackState": state.snapshot.playbackState,
        "matchStatus": state.matchStatus,
        "previous": position.previous.text if position.previous else None,
        "current": position.current.text if position.current else None,
        "next": position.next.text if position.next else None,
        "message": state.message,
    }


def find_panel_executable(explicit: Optional[Path] = None) -> Path:
    candidates = []
    if explicit is not None:
        candidates.append(Path(explicit))
    configured = os.environ.get("APPLYRX_PANEL_EXECUTABLE")
    if configured:
        candidates.append(Path(configured))
    candidates.extend((
        PROJECT_DIR / "build" / "ApplyrxLyricsPanel",
        PROJECT_DIR / "native" / ".build" / "release" / "ApplyrxLyricsPanel",
        Path(sys.executable).resolve().parent.parent / "Resources" / "native" / "ApplyrxLyricsPanel",
    ))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(
        "SwiftUI panel executable is unavailable; build it with scripts/build_app.sh."
    )


class PanelBridge:
    """Own the native panel process and feed it authoritative manager snapshots."""

    def __init__(
        self,
        executable: Optional[Path] = None,
        interval: float = 0.25,
        provider=None,
        watcher=None,
        manager_factory=None,
    ):
        self.executable = executable
        self.interval = max(0.1, interval)
        self.provider = provider
        self.watcher = watcher
        self.manager_factory = manager_factory
        self.process: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        if self.is_running:
            return
        if self.process is not None:
            self.stop()
        binary = find_panel_executable(self.executable)
        self.stop_event.clear()
        self.process = subprocess.Popen(
            [str(binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.thread = threading.Thread(
            target=self._feed_snapshots,
            name="applyrx-panel-state",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        self.process = None
        self.thread = None

    def _feed_snapshots(self) -> None:
        provider = self.provider or AppleMusicCacheProvider()
        watcher = self.watcher or NowPlayingWatcher()
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="panel-provider")
        manager = (
            self.manager_factory(provider, executor)
            if self.manager_factory
            else CurrentTrackManager(provider, executor=executor)
        )
        try:
            while not self.stop_event.is_set():
                process = self.process
                if process is None or process.poll() is not None or process.stdin is None:
                    return
                state = manager.update(watcher.poll())
                payload = json.dumps(
                    presentation_state(state),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8") + b"\n"
                try:
                    process.stdin.write(payload)
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    return
                self.stop_event.wait(self.interval)
        finally:
            manager.close()
            executor.shutdown(wait=False, cancel_futures=True)


def main() -> int:
    bridge = PanelBridge()
    try:
        bridge.start()
        while bridge.is_running:
            time.sleep(0.25)
        return 0
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        bridge.stop()


if __name__ == "__main__":
    raise SystemExit(main())
