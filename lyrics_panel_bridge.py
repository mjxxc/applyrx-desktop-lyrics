"""Bridge live lyrics-engine snapshots to the native SwiftUI panel."""

from __future__ import annotations

import json
import math
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
    current_line = position.current or position.previous
    lines = state.lyrics.lines if state.lyrics is not None else ()
    current_index = position.currentIndex
    if current_index is None and current_line is not None:
        current_index = next(
            (
                index for index, line in enumerate(lines)
                if line.startTime == current_line.startTime
            ),
            None,
        )
    current_lyric = None
    if current_line is not None:
        words = []
        valid_words = True
        try:
            for word in current_line.words or ():
                start = word.startTime
                end = word.endTime
                if (
                    isinstance(start, bool)
                    or not isinstance(start, (int, float))
                    or isinstance(end, bool)
                    or not isinstance(end, (int, float))
                    or not isinstance(word.text, str)
                    or not math.isfinite(start)
                    or not math.isfinite(end)
                    or start < current_line.startTime
                    or end > current_line.endTime
                    or end <= start
                ):
                    valid_words = False
                    break
                words.append({
                    "startTime": start,
                    "endTime": end,
                    "text": word.text,
                })
        except (AttributeError, TypeError):
            valid_words = False
        current_lyric = {
            "text": current_line.text,
            "startTime": current_line.startTime,
            "endTime": current_line.endTime,
            "words": words if valid_words else [],
        }
    previous_lines = []
    next_lines = []
    if current_line is not None and current_index is not None:
        previous_lines = [
            line.text for line in lines[max(0, current_index - 2):current_index]
        ]
        next_lines = [
            line.text for line in lines[current_index + 1:current_index + 3]
        ]
        previous = lines[current_index - 1] if current_index > 0 else None
        next_line = lines[current_index + 1] if current_index + 1 < len(lines) else None
    elif state.lyrics is not None and position.currentIndex is None and position.timestamp >= 0:
        if lines and position.timestamp < lines[0].startTime:
            next_lines = [line.text for line in lines[:2]]
            next_line = lines[0]
        else:
            next_line = position.next
        previous = None
    else:
        previous = None
        next_line = position.next
    return {
        "title": track.title if track else "",
        "artist": track.artist if track else "",
        "playbackState": state.snapshot.playbackState,
        "playbackPosition": position.timestamp if math.isfinite(position.timestamp) else None,
        "matchStatus": state.matchStatus,
        "lyricIndex": current_index if current_index is not None else -1,
        "seeked": state.snapshot.seeked,
        "trackChanged": state.snapshot.trackChanged,
        "previous": previous.text if previous else None,
        "previousLines": previous_lines,
        "current": current_line.text if current_line else None,
        "currentLyric": current_lyric,
        "next": next_line.text if next_line else None,
        "nextLines": next_lines,
        "message": state.message,
    }


def panel_message(
    state: ManagerState,
    *,
    panel_visible: Optional[bool] = None,
    open_settings: bool = False,
) -> dict:
    message = presentation_state(state)
    if panel_visible is not None:
        message["panelVisible"] = panel_visible
    message["openSettings"] = open_settings
    return message


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


def parse_panel_event(line: bytes) -> Optional[dict]:
    """Parse one JSON event line emitted by the native panel on stdout.

    The panel only writes here for user-initiated actions (currently the
    visibility hot key). Returns None for blank/unparsable input so a stray
    line can never break the reader loop.
    """
    if not line:
        return None
    try:
        payload = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


class PanelBridge:
    """Own the native panel process and feed it authoritative manager snapshots."""

    def __init__(
        self,
        executable: Optional[Path] = None,
        interval: float = 0.25,
        provider=None,
        watcher=None,
        manager_factory=None,
        on_event=None,
    ):
        self.executable = executable
        self.interval = max(0.1, interval)
        self.provider = provider
        self.watcher = watcher
        self.manager_factory = manager_factory
        self.on_event = on_event
        self.process: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.event_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self._control_lock = threading.Lock()
        self._panel_visible = True
        self._panel_visibility_pending = True
        self._open_settings = False

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, panel_visible: bool = True) -> None:
        if self.is_running:
            return
        if self.process is not None:
            self.stop()
        binary = find_panel_executable(self.executable)
        self.stop_event.clear()
        with self._control_lock:
            self._panel_visible = bool(panel_visible)
            self._panel_visibility_pending = True
            self._open_settings = False
        arguments = [str(binary)]
        if not panel_visible:
            arguments.append("--initially-hidden")
        self.process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.thread = threading.Thread(
            target=self._feed_snapshots,
            name="applyrx-panel-state",
            daemon=True,
        )
        self.thread.start()
        if self.process.stdout is not None:
            self.event_thread = threading.Thread(
                target=self._read_events,
                name="applyrx-panel-events",
                daemon=True,
            )
            self.event_thread.start()

    def set_panel_visible(self, visible: bool) -> None:
        with self._control_lock:
            visible = bool(visible)
            if self._panel_visible != visible:
                self._panel_visible = visible
                self._panel_visibility_pending = True

    def request_settings(self) -> None:
        with self._control_lock:
            self._open_settings = True

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
        if self.event_thread is not None and self.event_thread is not threading.current_thread():
            self.event_thread.join(timeout=2)
        self.process = None
        self.thread = None
        self.event_thread = None

    def _read_events(self) -> None:
        """Relay native panel events (e.g. hot-key visibility) to the host.

        Runs on its own thread because the panel writes these lines from its
        main thread; callbacks are expected to marshal to the UI thread.
        """
        process = self.process
        if process is None or process.stdout is None:
            return
        stream = process.stdout
        buffer = b""
        try:
            while not self.stop_event.is_set():
                chunk = stream.read(1)
                if not chunk:
                    return
                if chunk == b"\n":
                    line, buffer = buffer, b""
                    event = parse_panel_event(line)
                    if event is not None:
                        self._dispatch_event(event)
                else:
                    buffer += chunk
        except (OSError, ValueError):
            return
        finally:
            line, buffer = buffer, b""
            event = parse_panel_event(line)
            if event is not None:
                self._dispatch_event(event)

    def _dispatch_event(self, event: dict) -> None:
        callback = self.on_event
        if callback is None:
            return
        try:
            callback(event)
        except Exception:
            return

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
                with self._control_lock:
                    desired_visibility = self._panel_visible
                    visibility_pending = self._panel_visibility_pending
                    panel_visible = desired_visibility if visibility_pending else None
                    self._panel_visibility_pending = False
                    open_settings = self._open_settings
                    self._open_settings = False
                if not desired_visibility and not open_settings and not visibility_pending:
                    self.stop_event.wait(self.interval)
                    continue
                state = manager.update(watcher.poll())
                payload = json.dumps(
                    panel_message(
                        state,
                        panel_visible=panel_visible,
                        open_settings=open_settings,
                    ),
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
