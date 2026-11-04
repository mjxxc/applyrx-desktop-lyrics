"""Transparent hover capsule shown under the menu bar lyric icon.

The capsule is a display-only panel: it never becomes key, never takes mouse
events, and follows the current lyric live while it is open. The AppKit work is
kept here so ``menubar_lyrics.py`` stays importable without a GUI session.
"""

from __future__ import annotations

from AppKit import (
    NSAttributedString,
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSGlassEffectView,
    NSGlassEffectViewStyleClear,
    NSForegroundColorAttributeName,
    NSFontAttributeName,
    NSImage,
    NSLineBreakByTruncatingTail,
    NSShadow,
    NSShadowAttributeName,
    NSMakeRect,
    NSMakeSize,
    NSPanel,
    NSScreen,
    NSTextField,
    NSSize,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectStateActive,
    NSView,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
    NSStatusWindowLevel,
    NSTextAlignmentCenter,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
)

from menubar_lyrics import capsule_copy

CAPSULE_HEIGHT = 60.0
CAPSULE_RADIUS = 30.0
CAPSULE_MIN_WIDTH = 220.0
CAPSULE_MAX_WIDTH = 420.0
CAPSULE_PADDING = 18.0
LINE_GAP = 3.0
GAP_BELOW_MENU_BAR = 6.0
CURRENT_FONT_SIZE = 16.0
NEXT_FONT_SIZE = 12.0
NEXT_ALPHA = 0.62


def status_icon():
    """Template image for the menu bar item (adapts to light/dark menu bars)."""
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("music.note", "lyrics")
    if image is not None:
        image.setTemplate_(True)
    return image


class CapsulePanel:
    """Borderless, click-through glass capsule with two lyric rows."""

    def __init__(self):
        self._width = CAPSULE_MIN_WIDTH
        self._current_text = ""
        self._next_text = ""
        self._glass_view = None
        self._fallback_view = None

        self.window = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0.0, 0.0, CAPSULE_MIN_WIDTH, CAPSULE_HEIGHT),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setHasShadow_(True)
        self.window.setLevel_(NSStatusWindowLevel)
        self.window.setIgnoresMouseEvents_(True)
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        self.window.setAlphaValue_(0.0)
        self.window.setHidesOnDeactivate_(False)

        container = NSMakeRect(0.0, 0.0, CAPSULE_MIN_WIDTH, CAPSULE_HEIGHT)
        content = None
        try:
            glass = NSGlassEffectView.alloc().initWithFrame_(container)
            glass.setCornerRadius_(CAPSULE_RADIUS)
            try:
                glass.setStyle_(NSGlassEffectViewStyleClear)
            except Exception:
                pass
            try:
                glass.setTintColor_(NSColor.clearColor())
            except Exception:
                pass
            glass.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            self._glass_view = glass
            content = glass
        except Exception:
            fallback = NSVisualEffectView.alloc().initWithFrame_(container)
            fallback.setMaterial_(NSVisualEffectMaterialHUDWindow)
            fallback.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            fallback.setState_(NSVisualEffectStateActive)
            fallback.setWantsLayer_(True)
            fallback.layer().setCornerRadius_(CAPSULE_RADIUS)
            fallback.layer().setMasksToBounds_(True)
            fallback.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            self._fallback_view = fallback
            content = fallback
        self.window.setContentView_(content)

        self._paragraph_style = self._make_paragraph_style()
        self.current_label.setParagraphStyle_(self._paragraph_style) if False else None
        self._current_shadow = self._make_shadow(0.85)
        self._next_shadow = self._make_shadow(0.75)
        self._paragraph_style = self._make_paragraph_style()
        self.current_label = self._make_label(CURRENT_FONT_SIZE, NSFontWeightSemibold, 1.0, self._paragraph_style)
        self.next_label = self._make_label(NEXT_FONT_SIZE, NSFontWeightRegular, NEXT_ALPHA, self._paragraph_style)
        content.addSubview_(self.current_label)
        content.addSubview_(self.next_label)
        self._layout()

    @staticmethod
    def _make_shadow(alpha):
        # Black text on clear glass: a light halo keeps it legible when the
        # window behind is dark, the same trick Finder's desktop labels use.
        shadow = NSShadow.alloc().init()
        shadow.setShadowColor_(NSColor.whiteColor().colorWithAlphaComponent_(alpha))
        shadow.setShadowBlurRadius_(3.5)
        shadow.setShadowOffset_(NSSize(0.0, 1.0))
        return shadow

    @staticmethod
    def _make_paragraph_style():
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(NSTextAlignmentCenter)
        style.setLineBreakMode_(NSLineBreakByTruncatingTail)
        return style

    @staticmethod
    def _make_label(size, weight, alpha, paragraph_style):
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 10.0, size))
        field.setBezeled_(False)
        field.setDrawsBackground_(False)
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setUsesSingleLineMode_(True)
        field.setAlignment_(NSTextAlignmentCenter)
        field.setLineBreakMode_(NSLineBreakByTruncatingTail)
        font = NSFont.systemFontOfSize_weight_(size, weight)
        field.setFont_(font)
        field.setTextColor_(NSColor.blackColor().colorWithAlphaComponent_(alpha))
        return field

    @staticmethod
    def _attributed(text, field, shadow, paragraph_style):
        attrs = {
            NSFontAttributeName: field.font(),
            NSForegroundColorAttributeName: field.textColor(),
            NSShadowAttributeName: shadow,
            # Attributed text takes its alignment from the paragraph style, not
            # from the field, so centring has to be in the attributes.
            NSParagraphStyleAttributeName: paragraph_style,
        }
        return NSAttributedString.alloc().initWithString_attributes_(text, attrs)

    # ── content / layout ──────────────────────────────────────

    def update(self, current, next_line, limit=26):
        line1, line2 = capsule_copy(current, next_line, limit)
        if (line1, line2) == (self._current_text, self._next_text):
            return
        self._current_text, self._next_text = line1, line2
        self.current_label.setAttributedStringValue_(
            self._attributed(line1, self.current_label, self._current_shadow, self._paragraph_style)
        )
        self.next_label.setAttributedStringValue_(
            self._attributed(line2, self.next_label, self._next_shadow, self._paragraph_style)
        )
        self._layout()

    def _layout(self):
        self.current_label.sizeToFit()
        self.next_label.sizeToFit()
        current_h = self.current_label.frame().size.height
        next_h = self.next_label.frame().size.height
        natural = max(self.current_label.frame().size.width, self.next_label.frame().size.width) + CAPSULE_PADDING * 2.0
        width = max(CAPSULE_MIN_WIDTH, min(CAPSULE_MAX_WIDTH, natural))
        self._width = width
        inner = width - CAPSULE_PADDING * 2.0
        total = current_h + LINE_GAP + next_h
        top = (CAPSULE_HEIGHT - total) / 2.0
        self.current_label.setFrame_(NSMakeRect(CAPSULE_PADDING, top + next_h + LINE_GAP, inner, current_h))
        self.next_label.setFrame_(NSMakeRect(CAPSULE_PADDING, top, inner, next_h))
        self.window.setContentSize_(NSMakeSize(width, CAPSULE_HEIGHT))

    # ── position / visibility ────────────────────────────────

    def show(self, anchor_frame, screen=None):
        screen = screen or NSScreen.mainScreen()
        screen_frame = screen.frame() if screen is not None else NSMakeRect(0.0, 0.0, 1440.0, 900.0)
        visible = screen.visibleFrame() if screen is not None else screen_frame
        width = self._width
        x = anchor_frame.origin.x + anchor_frame.size.width / 2.0 - width / 2.0
        # visibleFrame already excludes the menu bar, so its top edge is the
        # exact line to hang the capsule below (works with a notch too).
        y = visible.origin.y + visible.size.height - GAP_BELOW_MENU_BAR - CAPSULE_HEIGHT
        x = max(screen_frame.origin.x + 8.0,
                min(x, screen_frame.origin.x + screen_frame.size.width - width - 8.0))
        y = max(screen_frame.origin.y + 8.0,
                min(y, screen_frame.origin.y + screen_frame.size.height - CAPSULE_HEIGHT - 8.0))
        target = NSMakeRect(x, y, width, CAPSULE_HEIGHT)
        # Called while playing (every UI tick), so only move when something
        # actually changed; repeated orderFront would fight the window server.
        if self.window.isVisible():
            current = self.window.frame()
            if (abs(current.origin.x - target.origin.x) < 0.5
                    and abs(current.origin.y - target.origin.y) < 0.5
                    and abs(current.size.width - target.size.width) < 0.5):
                return
        self.window.setFrame_display_(target, True)
        # No fade: the panel is a small HUD, and animating alpha has bitten
        # before when the run loop is not a full app loop (window stayed at 0).
        self.window.setAlphaValue_(1.0)
        self.window.orderFrontRegardless()

    def hide(self):
        if self.window is None or not self.window.isVisible():
            return
        self.window.setAlphaValue_(0.0)
        self.window.orderOut_(None)

    def close(self):
        self.hide()

    def is_visible(self):
        return self.window is not None and bool(self.window.isVisible())

