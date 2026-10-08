#!/usr/bin/env python3
"""Applyrx — Apple Music 桌面歌词 UI"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from functools import partial
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
VENV_DIR = SCRIPT_DIR / "venv"
VENV_PYTHON = VENV_DIR / "bin/python"
IS_BUNDLED_APP = bool(getattr(sys, "frozen", False))


def _find_project_dir(script_dir: Path, is_bundled: bool) -> Path:
    if not is_bundled:
        return script_dir
    # 向上找到 .app 边界，返回其父目录（即 /Applications）
    p = script_dir
    for _ in range(10):
        if p.suffix == ".app":
            return p.parent
        p = p.parent
    return script_dir


if not IS_BUNDLED_APP and VENV_PYTHON.exists() and Path(sys.prefix).resolve() != VENV_DIR.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

import objc
from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSEvent,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSScrollView,
    NSShadow,
    NSShadowAttributeName,
    NSSlider,
    NSStatusBar,
    NSString,
    NSTextView,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSScreen,
    NSFloatingWindowLevel,
)
from Foundation import NSMutableAttributedString, NSObject, NSMakeRange, NSTimer
from PyObjCTools import AppHelper

import main as core
from apple_music_watcher import AppleMusicWatcher
from lyrics_panel_bridge import PanelBridge


PANEL_WIDTH = 980
PANEL_HEIGHT = 112
NS_VARIABLE_STATUS_ITEM_LENGTH = -1
LOG_PATH = Path("/tmp/applyrx.log")
PROJECT_DIR = _find_project_dir(SCRIPT_DIR, IS_BUNDLED_APP)
PROJECT_HELPER = PROJECT_DIR / "lyrics_state.py"
HELPER = PROJECT_HELPER if PROJECT_HELPER.exists() else SCRIPT_DIR / "lyrics_state.py"
PROJECT_VENV_PYTHON = PROJECT_DIR / "venv/bin/python"
HELPER_PYTHON = PROJECT_VENV_PYTHON if PROJECT_VENV_PYTHON.exists() else Path(sys.executable)
CONFIG_DIR = Path.home() / ".applyrx"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "offset": 0.8,
    "panel_x_factor": 0.5,
    "bottom_margin": 150.0,
    "font_size_current": 33.0,
    "font_size_next": 22.0,
    "show_next_line": True,
    "panel_width": 980.0,
    "panel_height": 112.0,
    "background_visible": True,
    "background_alpha": 0.58,
    "hide_on_mouse": False,
    "desktop_visible": True,
    "desktop_click_through": True,
    "menubar_lyrics_visible": True,
    "auto_show_with_music": True,
    "auto_hide_with_music": True,
    "auto_show_while_playing": True,
}
CONFIG = dict(DEFAULT_CONFIG)
APP_DELEGATE = None

# Seconds a pause must persist before the lyrics are hidden. Apple Music flips
# to paused for a moment on some operations (seek, queue advance), so hiding on
# the first sample would make the panel flicker.
PAUSE_HIDE_DEBOUNCE = 0.4

# How often playback state is sampled while the native panel owns the window.
# The panel bridge already polls Apple Music on its own 250ms cadence; this keeps
# the visibility rule in step with that instead of hammering osascript from the
# 0.12s UI timer.
_PLAYBACK_POLL_INTERVAL = 0.25


def log(message: str) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(message + "\n")


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config.update({k: data[k] for k in DEFAULT_CONFIG if k in data})
    except Exception as exc:
        log(f"config load error: {exc}")
    return config


def save_config() -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log(f"config save error: {exc}")


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def nsfont(name: str, size: float, fallback_bold: bool = False):
    font = NSFont.fontWithName_size_(name, size)
    if font is not None:
        return font
    return NSFont.boldSystemFontOfSize_(size) if fallback_bold else NSFont.systemFontOfSize_(size)


def text_size(text: str, attrs: dict) -> tuple[float, float]:
    size = NSString.stringWithString_(text).sizeWithAttributes_(attrs)
    return float(size.width), float(size.height)


def short_text(text: str, limit: int = 24) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def draw_centered_text(text: str, y: float, container_width: float, font, color, shadow_color=None):
    if not text:
        return

    attrs = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: color,
    }
    if shadow_color is not None:
        shadow = NSShadow.alloc().init()
        shadow.setShadowBlurRadius_(3.2)
        shadow.setShadowOffset_((0, 0))
        shadow.setShadowColor_(shadow_color)
        attrs[NSShadowAttributeName] = shadow

    width, _ = text_size(text, attrs)
    x = (container_width - width) / 2
    NSString.stringWithString_(text).drawAtPoint_withAttributes_((x, y), attrs)


class FullLyricsTextView(NSTextView):
    def scrollWheel_(self, event):
        controller = getattr(self, "controller", None)
        if controller is not None:
            controller.auto_follow = False
        objc.super(FullLyricsTextView, self).scrollWheel_(event)

    def mouseDown_(self, event):
        controller = getattr(self, "controller", None)
        if controller is not None and event.clickCount() >= 2:
            controller.auto_follow = True
            controller.scrollToCurrentLine()
        objc.super(FullLyricsTextView, self).mouseDown_(event)


class FullLyricsWindow(NSObject):
    window = None
    text_view = None
    lines = None
    active_idx = -1
    line_ranges = None
    auto_follow = True

    def show(self):
        if self.window is None:
            self.buildWindow()
        self.window.makeKeyAndOrderFront_(None)

    def isVisible(self) -> bool:
        return bool(self.window is not None and self.window.isVisible())

    def close(self):
        if self.window is not None:
            self.window.orderOut_(None)

    def buildWindow(self):
        screen = NSScreen.mainScreen().visibleFrame()
        width = 520
        height = 640
        frame = NSMakeRect(
            screen.origin.x + screen.size.width - width - 72,
            screen.origin.y + 96,
            width,
            height,
        )
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskResizable
            | NSWindowStyleMaskMiniaturizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("Applyrx Lyrics")

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutoresizingMask_(18)

        self.text_view = FullLyricsTextView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        self.text_view.controller = self
        self.text_view.setEditable_(False)
        self.text_view.setSelectable_(True)
        self.text_view.setDrawsBackground_(False)
        self.text_view.setTextContainerInset_((24, 24))
        self.text_view.setFont_(nsfont("PingFangSC-Regular", 20))
        scroll.setDocumentView_(self.text_view)
        self.window.setContentView_(scroll)

    def updateWithPlayer_lines_index_error_(self, player, lines, active_idx, error):
        if self.window is None:
            return
        self.lines = lines or []
        self.active_idx = int(active_idx or 0)
        if not self.lines:
            msg = error or "请在 Apple Music 打开当前歌的歌词面板"
            self.text_view.textStorage().setAttributedString_(
                NSMutableAttributedString.alloc().initWithString_attributes_(
                    msg,
                    {
                        NSFontAttributeName: nsfont("PingFangSC-Regular", 20),
                        NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.74),
                    },
                )
            )
            return

        chunks = []
        self.line_ranges = []
        cursor = 0
        for line in self.lines:
            text = line.get("text") or " "
            chunks.append(text)
            self.line_ranges.append((cursor, len(text)))
            cursor += len(text)
            chunks.append("\n\n")
            cursor += 2

        payload = "".join(chunks)
        base_attrs = {
            NSFontAttributeName: nsfont("PingFangSC-Regular", 20),
            NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.54),
        }
        attr = NSMutableAttributedString.alloc().initWithString_attributes_(payload, base_attrs)
        if 0 <= self.active_idx < len(self.line_ranges):
            start, length = self.line_ranges[self.active_idx]
            attr.addAttributes_range_(
                {
                    NSFontAttributeName: nsfont("PingFangSC-Semibold", 26, True),
                    NSForegroundColorAttributeName: NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.55, 0.48, 1.0),
                },
                NSMakeRange(start, length),
            )
        self.text_view.textStorage().setAttributedString_(attr)
        if self.auto_follow:
            self.scrollToCurrentLine()

    def scrollToCurrentLine(self):
        if not self.line_ranges or self.text_view is None:
            return
        if 0 <= self.active_idx < len(self.line_ranges):
            start, _ = self.line_ranges[self.active_idx]
            self.text_view.scrollRangeToVisible_(NSMakeRange(start, 1))


class FloatingLyricsView(objc.lookUpClass("NSView")):
    def initWithFrame_(self, frame):
        self = objc.super(FloatingLyricsView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.player = {}
        self.lines = []
        self.meta = None
        self.error = ""
        self.active_idx = 0
        self.drag_start = None
        self.drag_config = None
        self.tracking_area = None
        return self

    def isOpaque(self):
        return False

    def pointIsInsidePanel_(self, point):
        panel = self.panelRect()
        return panel.origin.x <= point.x <= panel.origin.x + panel.size.width and panel.origin.y <= point.y <= panel.origin.y + panel.size.height

    def updateTrackingAreas(self):
        if self.tracking_area is not None:
            self.removeTrackingArea_(self.tracking_area)
        options = NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect
        self.tracking_area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            options,
            self,
            None,
        )
        self.addTrackingArea_(self.tracking_area)
        objc.super(FloatingLyricsView, self).updateTrackingAreas()

    def mouseEntered_(self, event):
        if bool(CONFIG["hide_on_mouse"]):
            self.window().setAlphaValue_(0.08)

    def mouseExited_(self, event):
        if bool(CONFIG["hide_on_mouse"]):
            self.window().setAlphaValue_(1.0)

    def updateWithPlayer_lines_meta_error_index_(self, player, lines, meta, error, active_idx):
        self.player = player or {}
        self.lines = lines or []
        self.meta = meta
        self.error = error or ""
        self.active_idx = int(active_idx or 0)
        self.setNeedsDisplay_(True)

    def panelRect(self):
        bounds = self.bounds()
        return NSMakeRect(0, 0, bounds.size.width, bounds.size.height)

    def mouseDown_(self, event):
        location = NSEvent.mouseLocation()
        frame = self.window().frame()
        self.drag_start = (float(location.x), float(location.y))
        self.drag_config = (float(frame.origin.x), float(frame.origin.y))

    def mouseDragged_(self, event):
        if self.drag_start is None or self.drag_config is None:
            objc.super(FloatingLyricsView, self).mouseDragged_(event)
            return
        location = NSEvent.mouseLocation()
        dx = float(location.x) - self.drag_start[0]
        dy = float(location.y) - self.drag_start[1]
        frame = self.window().frame()
        screen = NSScreen.mainScreen().visibleFrame()
        new_x = self.drag_config[0] + dx
        new_y = self.drag_config[1] + dy
        new_x = max(screen.origin.x + 8, min(new_x, screen.origin.x + screen.size.width - frame.size.width - 8))
        new_y = max(screen.origin.y + 8, min(new_y, screen.origin.y + screen.size.height - frame.size.height - 8))
        self.window().setFrame_display_(NSMakeRect(new_x, new_y, frame.size.width, frame.size.height), True)
        CONFIG["panel_x_factor"] = clamp((new_x + frame.size.width / 2 - screen.origin.x) / max(1.0, screen.size.width), 0.0, 1.0)
        CONFIG["bottom_margin"] = round(new_y - screen.origin.y, 1)
        save_config()
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        self.drag_start = None
        self.drag_config = None
        if APP_DELEGATE is not None:
            APP_DELEGATE.lockDesktopClickThrough_(None)

    def drawRect_(self, dirtyRect):
        try:
            self.drawLyrics_(dirtyRect)
        except Exception:
            log("draw error:\n" + traceback.format_exc())

    def drawLyrics_(self, dirtyRect):
        bounds = self.bounds()
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(bounds)

        panel = self.panelRect()
        center_x = panel.origin.x + panel.size.width / 2
        if bool(CONFIG["background_visible"]):
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(panel, 28, 28)
            NSColor.colorWithCalibratedWhite_alpha_(0.0, float(CONFIG["background_alpha"])).setFill()
            path.fill()

        title_font = nsfont("PingFangSC-Semibold", 18, True)
        lyric_font = nsfont("PingFangSC-Semibold", float(CONFIG["font_size_current"]), True)
        next_font = nsfont("PingFangSC-Regular", float(CONFIG["font_size_next"]))

        white = NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.94)
        dim = NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.52)
        shadow = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.95, 0.84, 0.75)

        name = self.player.get("name", "")
        artist = self.player.get("artist", "")
        title = f"{name}  —  {artist}".strip(" —")
        title_attrs = {
            NSFontAttributeName: title_font,
            NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.58),
        }
        title_w, _ = text_size(title, title_attrs)
        NSString.stringWithString_(title).drawAtPoint_withAttributes_(
            (center_x - title_w / 2, panel.origin.y + panel.size.height - 31),
            title_attrs,
        )

        if not self.lines:
            msg = self.error or "请在 Apple Music 打开当前歌的歌词面板"
            draw_centered_text(msg, panel.origin.y + 41, bounds.size.width, next_font, dim)
            return

        idx = min(max(0, self.active_idx), len(self.lines) - 1)
        line = self.lines[idx]
        next_line = self.lines[idx + 1]["text"] if bool(CONFIG["show_next_line"]) and idx + 1 < len(self.lines) else ""

        draw_centered_text(line["text"], panel.origin.y + panel.size.height * 0.38, bounds.size.width, lyric_font, white, shadow)
        draw_centered_text(next_line, panel.origin.y + panel.size.height * 0.12, bounds.size.width, next_font, dim)


class AppDelegate(NSObject):
    window = None
    view = None
    timer = None
    status_item = None
    desktop_menu_item = None
    settings_menu_item = None
    menubar_menu_item = None
    offset_menu_item = None
    font_size_menu_item = None
    desktop_size_slider = None
    background_menu_item = None
    drag_menu_item = None
    lyrics_window_menu_item = None
    launch_at_login_menu_item = None
    auto_show_menu_item = None
    auto_hide_menu_item = None
    playback_menu_item = None
    drag_unlock_timer = None
    desktop_click_through = True
    lyrics_window = None
    native_panel_bridge = None
    music_watcher = None
    # Session-only flag: never persisted to CONFIG. Tracks whether the user
    # explicitly hid the panel during this Applyrx session, so the Apple Music
    # auto-show does not fight a deliberate manual choice.
    desktop_hidden_manually = False
    # Playback-aware lyrics state. ``_paused_since`` holds the monotonic timestamp
    # of the first paused sample so a short pause can be cancelled before hiding;
    # ``_last_playback_state`` lets us ignore repeat samples of the same state.
    _paused_since = None
    _last_playback_state = None
    _playback_sync_at = 0.0
    last_key = None
    lines = None
    meta = None
    error = ""
    last_full_check_at = 0.0
    player = None
    anchor_position = 0.0
    anchor_monotonic = 0.0
    active_idx = 0

    def readHelperState(self):
        """直接在进程内调用 applyrx_state，不走子进程。"""
        import applyrx_state
        state = applyrx_state.read_state()
        return {
            "ok": state["ok"],
            "player": state["player"],
            "song_id": state["match"]["song_id"],
            "meta": state["match"]["meta"],
            "lines": state["lines"],
            "error": state["error"],
        }

    def applicationDidFinishLaunching_(self, notification):
        LOG_PATH.write_text("", encoding="utf-8")
        log("app started")
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.lines = []
        self.meta = None
        self.last_key = None
        self.lyrics_window = FullLyricsWindow.alloc().init()
        self.native_panel_bridge = PanelBridge()

        frame = self._desktopFrame()
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setHasShadow_(False)
        self.window.setLevel_(NSFloatingWindowLevel)
        self.setDesktopClickThroughEnabled_(bool(CONFIG["desktop_click_through"]))
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        self.view = FloatingLyricsView.alloc().initWithFrame_(NSMakeRect(0, 0, frame.size.width, frame.size.height))
        self.window.setContentView_(self.view)

        # v0.2.2: startup no longer trusts the saved visibility on its own.
        # Music is often already open but paused, and the panel must not appear
        # then, so it starts hidden and the first playback sample in tick_ decides
        # whether to show it. The panel process itself is still started so the
        # hot keys and settings keep working while hidden.
        CONFIG["desktop_visible"] = False
        self.desktop_hidden_manually = False

        self.makeStatusItem()
        self._resetPlaybackState()
        # Positional argument on purpose: PyObjC maps this method to a selector
        # that takes a single value, so a keyword would be rejected at runtime.
        self._startNativePanel(False)
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.12,
            self,
            objc.selector(self.tick_, signature=b"v@:@"),
            None,
            True,
        )
        self.timer.fire()
        self._startMusicWatcher()

    def _startMusicWatcher(self):
        """Begin watching the Apple Music lifecycle (idempotent)."""
        if self.music_watcher is None:
            self.music_watcher = AppleMusicWatcher(
                on_music_launched=self._onMusicLaunched,
                on_music_terminated=self._onMusicTerminated,
            )
        if self.music_watcher.is_running:
            return
        try:
            self.music_watcher.start()
            log("apple music watcher started")
        except Exception:
            # Lifecycle linkage is an enhancement; never block app startup on it.
            log("apple music watcher failed:\n" + traceback.format_exc())
            self.music_watcher = None

    def makeStatusItem(self):
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NS_VARIABLE_STATUS_ITEM_LENGTH)
        self.status_item.button().setTitle_("applyrx")
        menu = NSMenu.alloc().init()

        self.settings_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Settings…",
            "openSettings:",
            ",",
        )
        self.settings_menu_item.setTarget_(self)
        menu.addItem_(self.settings_menu_item)
        menu.addItem_(NSMenuItem.separatorItem())

        self.desktop_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Hide Lyrics" if bool(CONFIG["desktop_visible"]) else "Show Lyrics",
            "toggleDesktopLyrics:",
            "",
        )
        self.desktop_menu_item.setTarget_(self)
        menu.addItem_(self.desktop_menu_item)

        self.drag_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "临时移动桌面歌词（10秒）" if self.desktop_click_through else "完成移动并点穿",
            "toggleDesktopDragMode:",
            "m",
        )
        self.drag_menu_item.setTarget_(self)
        menu.addItem_(self.drag_menu_item)

        self.menubar_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "隐藏菜单栏歌词" if bool(CONFIG["menubar_lyrics_visible"]) else "显示菜单栏歌词",
            "toggleMenubarLyrics:",
            "",
        )
        self.menubar_menu_item.setTarget_(self)
        menu.addItem_(self.menubar_menu_item)

        self.offset_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Offset: {float(CONFIG['offset']):+.1f}s",
            None,
            "",
        )
        menu.addItem_(self.offset_menu_item)

        inc_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Offset +0.1s", "increaseOffset:", "]")
        inc_item.setTarget_(self)
        menu.addItem_(inc_item)

        dec_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Offset -0.1s", "decreaseOffset:", "[")
        dec_item.setTarget_(self)
        menu.addItem_(dec_item)

        self.font_size_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"桌面歌词大小: {float(CONFIG['font_size_current']):.0f}（左小右大）",
            None,
            "",
        )
        menu.addItem_(self.font_size_menu_item)

        slider_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        self.desktop_size_slider = NSSlider.alloc().initWithFrame_(NSMakeRect(14, 4, 230, 24))
        self.desktop_size_slider.setMinValue_(18.0)
        self.desktop_size_slider.setMaxValue_(72.0)
        self.desktop_size_slider.setDoubleValue_(float(CONFIG["font_size_current"]))
        self.desktop_size_slider.setTarget_(self)
        self.desktop_size_slider.setAction_("desktopSizeSliderChanged:")
        slider_item.setView_(self.desktop_size_slider)
        menu.addItem_(slider_item)

        reset_size_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("重置桌面歌词大小", "resetDesktopFontSize:", "0")
        reset_size_item.setTarget_(self)
        menu.addItem_(reset_size_item)

        self.background_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "隐藏桌面歌词背景" if bool(CONFIG["background_visible"]) else "显示桌面歌词背景",
            "toggleDesktopBackground:",
            "",
        )
        self.background_menu_item.setTarget_(self)
        menu.addItem_(self.background_menu_item)

        reload_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("重新匹配歌词", "reloadLyrics:", "r")
        reload_item.setTarget_(self)
        menu.addItem_(reload_item)

        full_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("打开完整歌词窗口", "toggleLyricsWindow:", "l")
        full_item.setTarget_(self)
        self.lyrics_window_menu_item = full_item
        menu.addItem_(full_item)

        menu.addItem_(NSMenuItem.separatorItem())

        self.auto_show_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "✓ 随 Apple Music 自动显示" if bool(CONFIG["auto_show_with_music"]) else "随 Apple Music 自动显示",
            "toggleAutoShowWithMusic:",
            "",
        )
        self.auto_show_menu_item.setTarget_(self)
        menu.addItem_(self.auto_show_menu_item)

        self.auto_hide_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "✓ 随 Apple Music 关闭自动隐藏" if bool(CONFIG["auto_hide_with_music"]) else "随 Apple Music 关闭自动隐藏",
            "toggleAutoHideWithMusic:",
            "",
        )
        self.auto_hide_menu_item.setTarget_(self)
        menu.addItem_(self.auto_hide_menu_item)

        self.playback_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "✓ 播放时显示、暂停时隐藏" if bool(CONFIG["auto_show_while_playing"]) else "播放时显示、暂停时隐藏",
            "toggleAutoShowWhilePlaying:",
            "",
        )
        self.playback_menu_item.setTarget_(self)
        menu.addItem_(self.playback_menu_item)

        menu.addItem_(NSMenuItem.separatorItem())

        self.launch_at_login_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "✓ 开机自启动" if self._is_login_item() else "开机自启动",
            "toggleLaunchAtLogin:", ""
        )
        self.launch_at_login_menu_item.setTarget_(self)
        menu.addItem_(self.launch_at_login_menu_item)

        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("退出 applyrx", "terminate:", "q")
        menu.addItem_(quit_item)
        self.status_item.setMenu_(menu)

    def _startNativePanel(self, *args):
        """Start the native panel process.

        Accepts an optional ``panel_visible`` argument so startup can bring the
        panel up already hidden; the hot keys and settings remain available
        either way.
        """
        panel_visible = bool(args[0]) if args else True
        try:
            if self.native_panel_bridge is not None and self.native_panel_bridge.on_event is None:
                self.native_panel_bridge.on_event = self._panelEventSink()
            self.native_panel_bridge.start(panel_visible=panel_visible)
            if self.window is not None:
                self.window.orderOut_(None)
            if self.lyrics_window is not None:
                self.lyrics_window.close()
            log(f"SwiftUI lyrics panel started (visible={panel_visible})")
            return True
        except Exception as exc:
            log(f"SwiftUI lyrics panel unavailable: {exc}")
            if self.window is not None and bool(CONFIG["desktop_visible"]):
                self.window.orderFrontRegardless()
            return False

    def _panelEventSink(self):
        """Return a zero-argument callback for the panel bridge.

        The bridge calls ``callback(event)``, but PyObjC rejects one-argument
        methods on this class, so the payload is bound here with functools.partial
        and handed over as a plain callable object.
        """
        return partial(_handle_panel_event, self)

    def refreshMenu(self):
        if self.desktop_menu_item is not None:
            self.desktop_menu_item.setTitle_("Hide Lyrics" if bool(CONFIG["desktop_visible"]) else "Show Lyrics")
        if self.menubar_menu_item is not None:
            self.menubar_menu_item.setTitle_("隐藏菜单栏歌词" if bool(CONFIG["menubar_lyrics_visible"]) else "显示菜单栏歌词")
        if self.drag_menu_item is not None:
            self.drag_menu_item.setTitle_("临时移动桌面歌词（10秒）" if self.desktop_click_through else "完成移动并点穿")
        if self.offset_menu_item is not None:
            self.offset_menu_item.setTitle_(f"Offset: {float(CONFIG['offset']):+.1f}s")
        if self.font_size_menu_item is not None:
            self.font_size_menu_item.setTitle_(f"桌面歌词大小: {float(CONFIG['font_size_current']):.0f}（左小右大）")
        if self.desktop_size_slider is not None:
            self.desktop_size_slider.setDoubleValue_(float(CONFIG["font_size_current"]))
        if self.background_menu_item is not None:
            self.background_menu_item.setTitle_("隐藏桌面歌词背景" if bool(CONFIG["background_visible"]) else "显示桌面歌词背景")
        if self.lyrics_window_menu_item is not None:
            visible = self.lyrics_window is not None and self.lyrics_window.isVisible()
            self.lyrics_window_menu_item.setTitle_("隐藏完整歌词窗口" if visible else "打开完整歌词窗口")
        if self.auto_show_menu_item is not None:
            self.auto_show_menu_item.setTitle_(
                "✓ 随 Apple Music 自动显示" if bool(CONFIG["auto_show_with_music"]) else "随 Apple Music 自动显示"
            )
        if self.auto_hide_menu_item is not None:
            self.auto_hide_menu_item.setTitle_(
                "✓ 随 Apple Music 关闭自动隐藏" if bool(CONFIG["auto_hide_with_music"]) else "随 Apple Music 关闭自动隐藏"
            )
        if self.playback_menu_item is not None:
            self.playback_menu_item.setTitle_(
                "✓ 播放时显示、暂停时隐藏" if bool(CONFIG["auto_show_while_playing"]) else "播放时显示、暂停时隐藏"
            )

    def _desktopFrame(self):
        screen = NSScreen.mainScreen().visibleFrame()
        width = max(320.0, min(float(CONFIG["panel_width"]), screen.size.width - 32.0))
        height = max(72.0, min(float(CONFIG["panel_height"]), screen.size.height - 32.0))
        center_x = screen.origin.x + screen.size.width * float(CONFIG["panel_x_factor"])
        x = center_x - width / 2.0
        y = screen.origin.y + float(CONFIG["bottom_margin"])
        x = max(screen.origin.x + 8.0, min(x, screen.origin.x + screen.size.width - width - 8.0))
        y = max(screen.origin.y + 8.0, min(y, screen.origin.y + screen.size.height - height - 8.0))
        return NSMakeRect(x, y, width, height)

    def _applyDesktopFrame(self):
        frame = self._desktopFrame()
        if self.window is not None:
            self.window.setFrame_display_(frame, True)
        if self.view is not None:
            self.view.setFrame_(NSMakeRect(0, 0, frame.size.width, frame.size.height))
            self.view.setNeedsDisplay_(True)

    # ── 开机自启动 ───────────────────────────────────────────

    def _app_path(self) -> str:
        """返回当前 .app bundle 路径，或源码路径。"""
        import os
        bundle = os.environ.get("APPLYRX_BUNDLE_PATH", "")
        if bundle:
            return bundle
        # py2app 运行时，__file__ 在 Contents/Resources 内
        resources = os.path.dirname(os.path.abspath(__file__))
        # 向上找 .app
        p = resources
        for _ in range(5):
            if p.endswith(".app"):
                return p
            p = os.path.dirname(p)
        return resources

    def _is_login_item(self) -> bool:
        import subprocess
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get the name of every login item'],
            capture_output=True, text=True
        )
        return "Applyrx" in result.stdout

    def toggleLaunchAtLogin_(self, sender):
        import subprocess
        if self._is_login_item():
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to delete login item "Applyrx"'],
                capture_output=True
            )
        else:
            path = self._app_path()
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to make login item at end '
                 f'with properties {{path:"{path}", hidden:false}}'],
                capture_output=True
            )
        # 刷新菜单标题
        if self.launch_at_login_menu_item is not None:
            self.launch_at_login_menu_item.setTitle_(
                "✓ 开机自启动" if self._is_login_item() else "开机自启动"
            )

    def toggleDesktopLyrics_(self, sender):
        self._setDesktopLyricsVisible(
            not bool(CONFIG["desktop_visible"]), True
        )

    def _setDesktopLyricsVisible(self, *args):
        """Single entry point for showing/hiding the desktop lyrics.

        Called as ``_setDesktopLyricsVisible(visible[, manual])``. Every caller
        (menu, hot key, Apple Music lifecycle) funnels through here so the panel
        is never started twice and the manual-override flag stays consistent with
        what the user currently sees.

        ``*args`` is required: PyObjC publishes every method to the ObjC runtime
        and rejects any signature it cannot map to a selector, which rules out
        ordinary optional parameters.
        """
        visible = bool(args[0]) if len(args) > 0 else False
        manual = bool(args[1]) if len(args) > 1 else False
        current = bool(CONFIG["desktop_visible"])
        if visible == current:
            # Already in the requested state. Still record the manual intent so a
            # redundant toggle cannot leave the flag contradicting the panel.
            if manual:
                self.desktop_hidden_manually = not visible
            self.refreshMenu()
            return

        CONFIG["desktop_visible"] = visible
        save_config()
        if visible:
            if self.native_panel_bridge is not None and self.native_panel_bridge.is_running:
                self.native_panel_bridge.set_panel_visible(True)
            elif not self._startNativePanel():
                if self.window is not None:
                    self.window.orderFrontRegardless()
        else:
            if self.native_panel_bridge is not None and self.native_panel_bridge.is_running:
                self.native_panel_bridge.set_panel_visible(False)
            elif self.native_panel_bridge is not None:
                self.native_panel_bridge.stop()
            if self.window is not None:
                self.window.orderOut_(None)

        if manual:
            self.desktop_hidden_manually = not visible
        self.refreshMenu()

    # ── playback-aware visibility ───────────────────────────────

    def _actualPanelVisible(self):
        """Return the panel's real visibility, or None when unknown.

        ``CONFIG["desktop_visible"]`` records what the host *asked for*, which
        can drift from the panel: the Swift side owns its own hot key and
        changes visibility without telling us first. When the native panel is
        attached, its own view of the world is authoritative; CONFIG is only a
        fallback for the AppKit fallback window.
        """
        bridge = self.native_panel_bridge
        if bridge is not None and bridge.is_running:
            visible = bridge.panel_visible
            if visible is not None:
                return bool(visible)
        return bool(CONFIG["desktop_visible"])

    def _syncPlaybackVisibility(self, *args):
        """Show the lyrics while Apple Music plays and hide them while paused.

        Called from ``tick_`` with the player snapshot, so it deliberately sits
        before the native-panel early return: when the Swift panel owns the
        window, ``tick_`` skips lyric rendering but still has to keep the
        visibility rule running.

        The decision order is: the manual override wins, then the playback state.
        A pause is debounced through ``PAUSE_HIDE_DEBOUNCE`` so the brief paused
        samples Apple Music emits during a seek or queue change cannot make the
        panel flicker. Resuming playback always shows immediately.

        Lyrics *matching* is deliberately not consulted — a track without lyrics
        is still a playing track, and that case is handled by the normal
        "no lyrics" path rather than by hiding the window.
        """
        player = args[0] if args else None
        if not isinstance(player, dict):
            return
        now = time.monotonic()

        if not player.get("running"):
            # Not running is handled by the lifecycle watcher; just reset so a
            # later pause does not inherit a stale timestamp.
            self._resetPlaybackState()
            return

        state = player.get("state")
        playing = state == "playing"

        if state != self._last_playback_state:
            self._last_playback_state = state
            if playing:
                self._paused_since = None
            elif state == "paused":
                self._paused_since = now

        if playing:
            if self._paused_since is not None:
                log("playback resumed before debounce: keeping lyrics visible")
                self._paused_since = None
            if not self.desktop_hidden_manually and not self._actualPanelVisible():
                self._setDesktopLyricsVisible(True)
            return

        if state != "paused":
            # "stopped" or an unknown state: nothing to do here. The existing
            # render path already reports未在播放 for those.
            return

        if not bool(CONFIG["auto_show_while_playing"]):
            return

        # Debounce: only hide once the pause has lasted long enough.
        if self._paused_since is None:
            self._paused_since = now
            return
        if now - self._paused_since < PAUSE_HIDE_DEBOUNCE:
            return
        # Already hidden for this pause; nothing left to do.
        if not self._actualPanelVisible():
            self._paused_since = None
            return
        # Hiding is an automatic consequence of pausing, never a manual choice,
        # so it must not overwrite the manual-override flag.
        self._setDesktopLyricsVisible(False)
        self._paused_since = None

    def _resetPlaybackState(self):
        """Clear playback tracking so a new session starts from a clean slate."""
        self._paused_since = None
        self._last_playback_state = None
        self._playback_sync_at = 0.0

    # ── Apple Music lifecycle ──────────────────────────────────

    def _onMusicLaunched(self):
        """React to Apple Music starting.

        v0.2.1 showed the lyrics straight away; v0.2.2 must not, because Music
        may open paused. The decision is therefore left to the playback rule in
        ``tick_``, which shows only once the player actually reports "playing".
        A failure to read the player here is not fatal either — the next tick
        retries.
        """
        try:
            if not bool(CONFIG["auto_show_with_music"]):
                return
            if self.desktop_hidden_manually:
                log("music launched: desktop lyrics stay hidden (manual override)")
                return
            self._resetPlaybackState()
            log("music launched: waiting for playback state")
        except Exception:
            log("onMusicLaunched error:\n" + traceback.format_exc())

    def _onMusicTerminated(self):
        """Auto-hide the desktop lyrics when Apple Music quits.

        Applyrx itself always stays resident in the menu bar; only the lyrics
        panel is hidden. Both the manual-override flag and the playback debounce
        are cleared so the next Music session starts from a clean slate.
        """
        try:
            self.desktop_hidden_manually = False
            self._resetPlaybackState()
            if not bool(CONFIG["auto_hide_with_music"]):
                return
            log("music terminated: hiding desktop lyrics (applyrx stays resident)")
            self._setDesktopLyricsVisible(False)
        except Exception:
            log("onMusicTerminated error:\n" + traceback.format_exc())

    def toggleAutoShowWithMusic_(self, sender):
        CONFIG["auto_show_with_music"] = not bool(CONFIG["auto_show_with_music"])
        save_config()
        log(f"auto_show_with_music: {bool(CONFIG['auto_show_with_music'])}")
        # Turning the option on while Music is already playing should take effect
        # immediately, but only when something is actually playing; otherwise the
        # playback rule in tick_ will show it as soon as playback starts.
        if bool(CONFIG["auto_show_with_music"]) and not self.desktop_hidden_manually:
            try:
                if (self.music_watcher is not None
                        and self.music_watcher.is_music_running()):
                    self._resetPlaybackState()
                    self._syncPlaybackVisibility(core.get_player_info())
            except Exception:
                log("auto show apply error:\n" + traceback.format_exc())
        self.refreshMenu()

    def toggleAutoHideWithMusic_(self, sender):
        CONFIG["auto_hide_with_music"] = not bool(CONFIG["auto_hide_with_music"])
        save_config()
        log(f"auto_hide_with_music: {bool(CONFIG['auto_hide_with_music'])}")
        self.refreshMenu()

    def toggleAutoShowWhilePlaying_(self, sender):
        CONFIG["auto_show_while_playing"] = not bool(CONFIG["auto_show_while_playing"])
        save_config()
        log(f"auto_show_while_playing: {bool(CONFIG['auto_show_while_playing'])}")
        # Enabling the rule while a track is already playing should show the
        # lyrics right away; disabling it should stop enforcing the pause rule.
        try:
            self._resetPlaybackState()
            if bool(CONFIG["auto_show_while_playing"]) and not self.desktop_hidden_manually:
                self._syncPlaybackVisibility(self.player or core.get_player_info())
        except Exception:
            log("auto playback apply error:\n" + traceback.format_exc())
        self.refreshMenu()

    def openSettings_(self, sender):
        if self.native_panel_bridge is None:
            self.native_panel_bridge = PanelBridge()
        if not self.native_panel_bridge.is_running:
            self.native_panel_bridge.start(
                panel_visible=bool(CONFIG["desktop_visible"])
            )
        self.native_panel_bridge.request_settings()

    def setDesktopClickThroughEnabled_(self, enabled):
        self.desktop_click_through = bool(enabled)
        if self.window is not None:
            self.window.setIgnoresMouseEvents_(self.desktop_click_through)
        log(f"desktop click-through: {self.desktop_click_through}")
        self.refreshMenu()

    def _cancelDragUnlockTimer(self):
        if self.drag_unlock_timer is not None:
            self.drag_unlock_timer.invalidate()
            self.drag_unlock_timer = None

    def toggleDesktopDragMode_(self, sender):
        if self.desktop_click_through:
            self._cancelDragUnlockTimer()
            self.setDesktopClickThroughEnabled_(False)
            if self.window is not None and bool(CONFIG["desktop_visible"]):
                self.window.orderFrontRegardless()
            self.drag_unlock_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                10.0,
                self,
                objc.selector(self.lockDesktopClickThrough_, signature=b"v@:@"),
                None,
                False,
            )
        else:
            self.lockDesktopClickThrough_(None)

    def lockDesktopClickThrough_(self, timer):
        self._cancelDragUnlockTimer()
        self.setDesktopClickThroughEnabled_(True)

    def toggleMenubarLyrics_(self, sender):
        CONFIG["menubar_lyrics_visible"] = not bool(CONFIG["menubar_lyrics_visible"])
        save_config()
        if self.status_item is not None and not bool(CONFIG["menubar_lyrics_visible"]):
            self.status_item.button().setTitle_("applyrx")
        self.refreshMenu()

    def increaseOffset_(self, sender):
        CONFIG["offset"] = round(float(CONFIG["offset"]) + 0.1, 2)
        save_config()
        self.refreshMenu()

    def decreaseOffset_(self, sender):
        CONFIG["offset"] = round(float(CONFIG["offset"]) - 0.1, 2)
        save_config()
        self.refreshMenu()

    def _set_desktop_size(self, size):
        current = max(18.0, min(72.0, float(size)))
        scale = current / float(DEFAULT_CONFIG["font_size_current"])
        CONFIG["font_size_current"] = round(current, 1)
        CONFIG["font_size_next"] = round(max(14.0, min(48.0, current * 0.67)), 1)
        CONFIG["panel_width"] = round(max(420.0, min(1800.0, float(DEFAULT_CONFIG["panel_width"]) * scale)), 1)
        CONFIG["panel_height"] = round(max(80.0, min(260.0, float(DEFAULT_CONFIG["panel_height"]) * scale)), 1)
        save_config()
        self._applyDesktopFrame()
        if self.view is not None:
            self.view.setNeedsDisplay_(True)
        self.refreshMenu()

    def desktopSizeSliderChanged_(self, sender):
        self._set_desktop_size(float(sender.doubleValue()))

    def resetDesktopFontSize_(self, sender):
        self._set_desktop_size(float(DEFAULT_CONFIG["font_size_current"]))

    def toggleDesktopBackground_(self, sender):
        CONFIG["background_visible"] = not bool(CONFIG["background_visible"])
        save_config()
        if self.view is not None:
            self.view.setNeedsDisplay_(True)
        self.refreshMenu()

    def reloadLyrics_(self, sender):
        self.lines = []
        self.meta = None
        self.error = "正在重新匹配 Apple Music 歌词..."
        self.last_key = None
        self.last_full_check_at = 0.0

    def toggleLyricsWindow_(self, sender):
        if self.lyrics_window is None:
            self.lyrics_window = FullLyricsWindow.alloc().init()
        if self.lyrics_window.isVisible():
            self.lyrics_window.close()
        else:
            self.lyrics_window.show()
            self.lyrics_window.updateWithPlayer_lines_index_error_(self.player or {}, self.lines or [], self.active_idx, self.error)
        self.refreshMenu()

    def _effective_player(self, player, now):
        player = dict(player or {})
        if player.get("state") == "playing":
            player["position"] = self.anchor_position + (now - self.anchor_monotonic)
        return player

    def _render_state(self, player):
        position = float(player.get("position") or 0) + float(CONFIG["offset"])
        self.active_idx = core.active_index(self.lines, position) if self.lines else 0
        self.view.updateWithPlayer_lines_meta_error_index_(player, self.lines or [], self.meta, self.error, self.active_idx)
        if self.lyrics_window is not None:
            self.lyrics_window.updateWithPlayer_lines_index_error_(player, self.lines or [], self.active_idx, self.error)
        if self.status_item is not None:
            if bool(CONFIG["menubar_lyrics_visible"]) and self.lines:
                text = self.lines[min(self.active_idx, len(self.lines) - 1)].get("text", "")
                self.status_item.button().setTitle_(short_text(text, 28))
            else:
                self.status_item.button().setTitle_("applyrx")

    def _pollPlaybackWhileNativePanelRuns(self):
        """Keep the playback rule alive while the Swift panel owns the window.

        ``tick_`` returns early in that mode, so playback state is sampled here
        instead. Sampling is throttled to ``_PLAYBACK_POLL_INTERVAL`` to match the
        cost of the AppleScript call rather than the 0.12s timer, and it reuses
        ``get_player_info()`` exactly like the rest of the app — no new poller.
        """
        now = time.monotonic()
        if now - self._playback_sync_at < _PLAYBACK_POLL_INTERVAL:
            return
        self._playback_sync_at = now
        try:
            player = core.get_player_info()
        except Exception:
            return
        self.player = player
        self._syncPlaybackVisibility(player)

    def tick_(self, timer):
        try:
            if (self.native_panel_bridge is not None
                    and self.native_panel_bridge.is_running):
                # The native panel renders the lyrics itself, but the playback
                # rule still has to run here: while the panel is up, this is the
                # only place that learns whether Music is playing or paused.
                self._pollPlaybackWhileNativePanelRuns()
                return
            now = time.monotonic()
            if self.lines and self.player and now - self.last_full_check_at < 1.0:
                self._render_state(self._effective_player(self.player, now))
                self._syncPlaybackVisibility(self.player)
                return

            self.last_full_check_at = now
            player = core.get_player_info()
            self.player = player
            self.anchor_position = float(player.get("position") or 0)
            self.anchor_monotonic = now
            self._syncPlaybackVisibility(player)

            if not player.get("running"):
                self.lines = []
                self.error = "Apple Music 未在运行"
                self._render_state(player)
                return

            if player.get("state") == "stopped":
                self.lines = []
                self.error = "未在播放"
                self._render_state(player)
                return

            key = (player.get("name", ""), player.get("artist", ""), round(float(player.get("duration") or 0), 1))
            if key != self.last_key:
                self.last_key = key
                self.lines = []
                self.meta = None
                self.error = "正在匹配 Apple Music 歌词..."
                log(f"track changed: {key}")

            if not self.lines:
                state = self.readHelperState()
                if state.get("ok"):
                    self.lines = state.get("lines") or []
                    self.meta = state.get("meta")
                    self.error = ""
                    log(f"matched: {state.get('song_id')} {self.meta} lines={len(self.lines)}")
                else:
                    self.error = state.get("error") or "未匹配到歌词"
                    log(f"match error: {self.error}")

            self._render_state(player)
            self.refreshMenu()
        except Exception:
            log("tick error:\n" + traceback.format_exc())

    def applicationWillTerminate_(self, notification):
        if self.native_panel_bridge is not None:
            self.native_panel_bridge.stop()
        if self.music_watcher is not None:
            try:
                self.music_watcher.stop()
            except Exception:
                log("watcher stop error:\n" + traceback.format_exc())
            self.music_watcher = None


def _handle_panel_event(delegate, event):
    """Handle user-initiated actions reported by the native panel.

    The panel owns its own hot keys, so a ⌃⌥⌘L toggle happens entirely in the
    child process. Without this bridge the host would keep a stale
    ``desktop_visible`` and could skip the very command that should re-show the
    panel. Lives at module scope so PyObjC does not try to publish it as an ObjC
    method, and hops to the main queue because the bridge reads it off-thread.
    """
    if delegate is None:
        return
    if not isinstance(event, dict) or event.get("event") != "visibilityChanged":
        return
    visible = bool(event.get("visible"))

    def apply() -> None:
        try:
            CONFIG["desktop_visible"] = visible
            save_config()
            # Keep the bridge's view in step too, since _actualPanelVisible()
            # treats it as the source of truth while the panel is attached.
            bridge = delegate.native_panel_bridge
            if bridge is not None:
                bridge.adopt_panel_visible(visible)
            # The user pressed the hot key, so this is an explicit choice and
            # must win over the Apple Music auto-show.
            delegate.desktop_hidden_manually = not visible
            log(f"panel visibility from hotkey: {visible}")
            delegate.refreshMenu()
        except Exception:
            log("panel event error:\n" + traceback.format_exc())

    try:
        from Foundation import NSOperationQueue

        NSOperationQueue.mainQueue().addOperationWithBlock_(apply)
    except Exception:
        apply()


# ``_panelEventSink`` gives the bridge a zero-argument callable, so PyObjC never
# sees a one-argument method on AppDelegate and the class still defines cleanly.
def _make_panel_event_sink(delegate):
    return delegate._panelEventSink()


def main():
    global CONFIG, APP_DELEGATE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offset", type=float, default=None, help="Lyric offset in seconds. Positive shows lyrics earlier.")
    parser.add_argument("--font-size", type=float, default=None, help="Current lyric font size.")
    parser.add_argument("--hide-next", action="store_true", help="Hide next lyric line.")
    args = parser.parse_args()

    CONFIG = load_config()
    if args.offset is not None:
        CONFIG["offset"] = args.offset
    if args.font_size is not None:
        CONFIG["font_size_current"] = args.font_size
    if args.hide_next:
        CONFIG["show_next_line"] = False
    save_config()

    app = NSApplication.sharedApplication()
    delegate = AppDelegate.alloc().init()
    APP_DELEGATE = delegate
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
