#!/usr/bin/env python3
"""LyricsX-style macOS UI for applyrx."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
VENV_DIR = SCRIPT_DIR / "venv"
VENV_PYTHON = VENV_DIR / "bin/python"
IS_BUNDLED_APP = bool(getattr(sys, "frozen", False))

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


WINDOW_HEIGHT = 340
PANEL_WIDTH = 980
PANEL_HEIGHT = 112
NS_VARIABLE_STATUS_ITEM_LENGTH = -1
LOG_PATH = Path("/tmp/applyrx_lyricsx_style.log")
PROJECT_DIR = SCRIPT_DIR.parents[3] if IS_BUNDLED_APP else SCRIPT_DIR
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
    "menubar_lyrics_visible": True,
}
CONFIG = dict(DEFAULT_CONFIG)
APP_DELEGATE = None


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
        self.error = "LyricsX"
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
        panel_width = min(PANEL_WIDTH, bounds.size.width - 32)
        panel_width = min(float(CONFIG["panel_width"]), bounds.size.width - 32)
        panel_height = float(CONFIG["panel_height"])
        panel_x = bounds.size.width * float(CONFIG["panel_x_factor"]) - panel_width / 2
        panel_x = max(16, min(panel_x, bounds.size.width - panel_width - 16))
        panel_y = float(CONFIG["bottom_margin"])
        panel_y = max(20, min(panel_y, bounds.size.height - panel_height - 18))
        return NSMakeRect(panel_x, panel_y, panel_width, panel_height)

    def mouseDown_(self, event):
        location = event.locationInWindow()
        if self.pointIsInsidePanel_(location):
            self.drag_start = (float(location.x), float(location.y))
            self.drag_config = (float(CONFIG["panel_x_factor"]), float(CONFIG["bottom_margin"]))
        else:
            objc.super(FloatingLyricsView, self).mouseDown_(event)

    def mouseDragged_(self, event):
        if self.drag_start is None or self.drag_config is None:
            objc.super(FloatingLyricsView, self).mouseDragged_(event)
            return
        location = event.locationInWindow()
        bounds = self.bounds()
        dx = float(location.x) - self.drag_start[0]
        dy = float(location.y) - self.drag_start[1]
        CONFIG["panel_x_factor"] = clamp(self.drag_config[0] + dx / max(1.0, bounds.size.width), 0.05, 0.95)
        CONFIG["bottom_margin"] = max(20.0, min(self.drag_config[1] + dy, bounds.size.height - float(CONFIG["panel_height"]) - 18))
        save_config()
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        self.drag_start = None
        self.drag_config = None

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
    menubar_menu_item = None
    offset_menu_item = None
    font_size_menu_item = None
    desktop_size_slider = None
    background_menu_item = None
    lyrics_window_menu_item = None
    lyrics_window = None
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
        env = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"):
            env.pop(key, None)
        env.setdefault("LANG", "en_US.UTF-8")
        env.setdefault("LC_ALL", "en_US.UTF-8")

        result = subprocess.run(
            [str(HELPER_PYTHON), str(HELPER), "--once"],
            cwd=str(PROJECT_DIR),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"lyrics_state exited {result.returncode}")
        return json.loads(result.stdout)

    def applicationDidFinishLaunching_(self, notification):
        LOG_PATH.write_text("", encoding="utf-8")
        log("app started")
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.lines = []
        self.meta = None
        self.last_key = None
        self.lyrics_window = FullLyricsWindow.alloc().init()

        screen = NSScreen.mainScreen().visibleFrame()
        frame = NSMakeRect(screen.origin.x, screen.origin.y, screen.size.width, WINDOW_HEIGHT)
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
        self.window.setIgnoresMouseEvents_(False)
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        self.view = FloatingLyricsView.alloc().initWithFrame_(NSMakeRect(0, 0, frame.size.width, frame.size.height))
        self.window.setContentView_(self.view)
        if bool(CONFIG["desktop_visible"]):
            self.window.orderFrontRegardless()

        self.makeStatusItem()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.12,
            self,
            objc.selector(self.tick_, signature=b"v@:@"),
            None,
            True,
        )
        self.timer.fire()

    def makeStatusItem(self):
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NS_VARIABLE_STATUS_ITEM_LENGTH)
        self.status_item.button().setTitle_("applyrx")
        menu = NSMenu.alloc().init()

        self.desktop_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "隐藏桌面歌词" if bool(CONFIG["desktop_visible"]) else "显示桌面歌词",
            "toggleDesktopLyrics:",
            "",
        )
        self.desktop_menu_item.setTarget_(self)
        menu.addItem_(self.desktop_menu_item)

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
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("退出 applyrx", "terminate:", "q")
        menu.addItem_(quit_item)
        self.status_item.setMenu_(menu)

    def refreshMenu(self):
        if self.desktop_menu_item is not None:
            self.desktop_menu_item.setTitle_("隐藏桌面歌词" if bool(CONFIG["desktop_visible"]) else "显示桌面歌词")
        if self.menubar_menu_item is not None:
            self.menubar_menu_item.setTitle_("隐藏菜单栏歌词" if bool(CONFIG["menubar_lyrics_visible"]) else "显示菜单栏歌词")
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

    def toggleDesktopLyrics_(self, sender):
        CONFIG["desktop_visible"] = not bool(CONFIG["desktop_visible"])
        save_config()
        if bool(CONFIG["desktop_visible"]):
            self.window.orderFrontRegardless()
        else:
            self.window.orderOut_(None)
        self.refreshMenu()

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

    def tick_(self, timer):
        try:
            now = time.monotonic()
            if self.lines and self.player and now - self.last_full_check_at < 1.0:
                self._render_state(self._effective_player(self.player, now))
                return

            self.last_full_check_at = now
            player = core.get_player_info()
            self.player = player
            self.anchor_position = float(player.get("position") or 0)
            self.anchor_monotonic = now

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
