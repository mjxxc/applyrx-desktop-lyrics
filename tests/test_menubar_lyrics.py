import unittest

from menubar_lyrics import (
    DEFAULT_MAX_CHARS,
    MODE_CAPSULE,
    MODE_ICON,
    MODE_TEXT,
    capsule_copy,
    format_line,
    normalize_mode,
    should_show_capsule,
    status_item_copy,
)


class MenuBarModeTests(unittest.TestCase):
    def test_known_modes_are_kept(self):
        for mode in (MODE_CAPSULE, MODE_TEXT, MODE_ICON):
            self.assertEqual(mode, normalize_mode(mode))

    def test_mode_is_normalized_and_trimmed(self):
        self.assertEqual(MODE_TEXT, normalize_mode("  TEXT "))
        self.assertEqual(MODE_ICON, normalize_mode("Icon"))

    def test_invalid_mode_falls_back_to_the_default(self):
        self.assertEqual(MODE_CAPSULE, normalize_mode("bogus"))
        self.assertEqual(MODE_CAPSULE, normalize_mode(None))
        self.assertEqual(MODE_CAPSULE, normalize_mode(3))

    def test_legacy_visibility_setting_is_migrated(self):
        # v0.2.4 configs only had menubar_lyrics_visible; leaving it off must not
        # resurrect the old text mode, it degrades to the icon-only item.
        self.assertEqual(MODE_ICON, normalize_mode(None, legacy_visible=False))
        self.assertEqual(MODE_CAPSULE, normalize_mode(None, legacy_visible=True))
        # An explicit v0.2.5 mode always wins over the legacy flag.
        self.assertEqual(MODE_TEXT, normalize_mode(MODE_TEXT, legacy_visible=False))


class CapsuleCopyTests(unittest.TestCase):
    def test_short_lines_pass_through(self):
        self.assertEqual(("a b", "c d"), capsule_copy("a b", "c d"))

    def test_whitespace_is_collapsed(self):
        self.assertEqual(("hello world", ""), capsule_copy("  hello \n world  ", None))

    def test_long_lines_are_truncated_with_an_ellipsis(self):
        line = format_line("一" * 40, 10)
        self.assertEqual(10, len(line))
        self.assertTrue(line.endswith("…"))

    def test_empty_values_are_safe(self):
        self.assertEqual(("", ""), capsule_copy(None, ""))
        self.assertEqual("", format_line("   "))
        self.assertEqual("", format_line("abc", 0))

    def test_default_limit_is_used(self):
        text = "长" * (DEFAULT_MAX_CHARS + 5)
        self.assertEqual(DEFAULT_MAX_CHARS, len(capsule_copy(text, "")[0]))


class CapsuleVisibilityTests(unittest.TestCase):
    def test_capsule_only_shows_while_playing_with_lyrics(self):
        self.assertTrue(should_show_capsule(True, True, MODE_CAPSULE))
        self.assertFalse(should_show_capsule(False, True, MODE_CAPSULE))
        self.assertFalse(should_show_capsule(True, False, MODE_CAPSULE))
        self.assertFalse(should_show_capsule(True, True, MODE_TEXT))
        self.assertFalse(should_show_capsule(True, True, MODE_ICON))


class StatusItemTests(unittest.TestCase):
    def test_text_mode_shows_the_current_line_or_a_fallback(self):
        self.assertEqual((False, "当前歌词"), status_item_copy(MODE_TEXT, "当前歌词"))
        self.assertEqual((False, "applyrx"), status_item_copy(MODE_TEXT, ""))

    def test_icon_modes_hide_the_title(self):
        self.assertEqual((True, ""), status_item_copy(MODE_CAPSULE, "当前歌词"))
        self.assertEqual((True, ""), status_item_copy(MODE_ICON, "当前歌词"))

    def test_text_mode_still_truncates(self):
        title = status_item_copy(MODE_TEXT, "字" * 60)[1]
        self.assertEqual(28, len(title))
        self.assertTrue(title.endswith("…"))


if __name__ == "__main__":
    unittest.main()


class ConfigMigrationTests(unittest.TestCase):
    """v0.2.5 moves the automatic lyric surface from the panel to the menu bar."""

    def test_old_config_turns_the_desktop_panel_off(self):
        import json
        import tempfile
        from pathlib import Path

        import applyrx_ui

        with tempfile.TemporaryDirectory() as tmp:
            original = applyrx_ui.CONFIG_PATH
            try:
                applyrx_ui.CONFIG_PATH = Path(tmp) / "config.json"
                applyrx_ui.CONFIG_PATH.write_text(json.dumps({
                    "auto_show_with_music": True,
                    "auto_show_while_playing": True,
                    "desktop_visible": True,
                }), encoding="utf-8")
                config = applyrx_ui.load_config()
            finally:
                applyrx_ui.CONFIG_PATH = original
        self.assertFalse(config["auto_show_with_music"])
        self.assertFalse(config["auto_show_while_playing"])
        self.assertFalse(config["desktop_visible"])
        self.assertEqual(3, config["config_version"])

    def test_a_deliberate_choice_is_kept_after_migration(self):
        import json
        import tempfile
        from pathlib import Path

        import applyrx_ui

        with tempfile.TemporaryDirectory() as tmp:
            original = applyrx_ui.CONFIG_PATH
            try:
                applyrx_ui.CONFIG_PATH = Path(tmp) / "config.json"
                applyrx_ui.CONFIG_PATH.write_text(json.dumps({
                    "config_version": 3,
                    "auto_show_while_playing": True,
                    "desktop_visible": True,
                }), encoding="utf-8")
                config = applyrx_ui.load_config()
            finally:
                applyrx_ui.CONFIG_PATH = original
        self.assertTrue(config["auto_show_while_playing"])
        self.assertTrue(config["desktop_visible"])


class PlayDrivenCapsuleTests(unittest.TestCase):
    """v0.2.5: playing shows the capsule, pause/quit hides it. No hover."""

    def setUp(self):
        from unittest.mock import MagicMock

        import applyrx_ui

        applyrx_ui.CONFIG = dict(applyrx_ui.DEFAULT_CONFIG)
        self.delegate = applyrx_ui.AppDelegate.alloc().init()
        self.capsule = MagicMock()
        self.capsule.is_visible.return_value = False
        self.delegate.menubar_capsule = self.capsule
        self.delegate.status_item = MagicMock()
        self.delegate._status_item_screen_rect = lambda: applyrx_ui.NSMakeRect(100.0, 100.0, 27.0, 33.0)
        self.delegate._menubar_is_playing = lambda: True
        self.delegate._menubar_lyric_pair = lambda: ("当前句", "下一句")

    def tearDown(self):
        import applyrx_ui

        applyrx_ui.CONFIG = dict(applyrx_ui.DEFAULT_CONFIG)

    def test_playing_shows_the_capsule(self):
        self.delegate._apply_menubar_state()
        self.capsule.update.assert_called_once_with("当前句", "下一句")
        self.capsule.show.assert_called_once()

    def test_paused_hides_the_capsule(self):
        self.capsule.is_visible.return_value = True
        self.delegate._menubar_is_playing = lambda: False
        self.delegate._apply_menubar_state()
        self.capsule.hide.assert_called_once()
        self.capsule.show.assert_not_called()

    def test_no_lyrics_hides_the_capsule(self):
        self.capsule.is_visible.return_value = True
        self.delegate._menubar_lyric_pair = lambda: ("", "")
        self.delegate._apply_menubar_state()
        self.capsule.hide.assert_called_once()

    def test_invalid_rect_hides_a_visible_capsule(self):
        self.capsule.is_visible.return_value = True
        self.delegate._status_item_screen_rect = lambda: None
        self.delegate._apply_menubar_state()
        self.capsule.hide.assert_called_once()


class LyricColorTests(unittest.TestCase):
    """The status bar menu offers a small fixed palette of lyric colours."""

    def test_known_colours_are_kept(self):
        from menubar_lyrics import LYRIC_COLORS, normalize_lyric_color

        for key, _label, _hex in LYRIC_COLORS:
            self.assertEqual(key, normalize_lyric_color(key))

    def test_unknown_and_empty_fall_back_to_black(self):
        from menubar_lyrics import normalize_lyric_color

        self.assertEqual("black", normalize_lyric_color("purple"))
        self.assertEqual("black", normalize_lyric_color(None))
        self.assertEqual("black", normalize_lyric_color(42))

    def test_hex_lookup(self):
        from menubar_lyrics import lyric_color_hex

        self.assertEqual("#FF3B30", lyric_color_hex("red"))
        self.assertEqual("#111111", lyric_color_hex("nonsense"))

    def test_menu_handler_applies_the_colour(self):
        from unittest.mock import MagicMock

        import applyrx_ui

        applyrx_ui.CONFIG = dict(applyrx_ui.DEFAULT_CONFIG)
        delegate = applyrx_ui.AppDelegate.alloc().init()
        delegate.menubar_capsule = MagicMock()
        delegate.refreshMenu = MagicMock()
        sender = MagicMock()
        sender.tag.return_value = 2  # red in LYRIC_COLORS
        delegate.setLyricsColor_(sender)
        self.assertEqual("red", applyrx_ui.CONFIG["lyrics_color"])
        delegate.menubar_capsule.set_lyrics_color.assert_called_once_with("red")
