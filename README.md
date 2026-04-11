# Applyrx

Applyrx is a macOS Apple Music live lyrics app inspired by LyricsX. It reads the
current Apple Music track, matches the song against Apple Music TTML lyric cache,
and renders synchronized lyrics as a floating desktop overlay, a menu bar lyric,
and a CLI-friendly JSON interface.

The project intentionally prefers strict matching over guessing: if the current
song cannot be clearly matched by Apple song id, metadata, and duration, Applyrx
shows a clear error instead of displaying lyrics from the wrong song.

## Features

- Apple Music synchronized lyrics from the local TTML cache.
- Floating desktop lyrics with draggable position.
- Menu bar lyric that can be shown or hidden.
- Full lyrics window with current-line highlighting.
- Desktop lyric size slider: left is smaller, right is larger.
- Desktop background can be shown or hidden.
- CLI for scripting and downstream integrations.
- Local JSON config at `~/.applyrx/config.json`.

## Requirements

- macOS
- Apple Music.app
- Python 3.11+ recommended
- Apple Music lyric panel opened at least once for the current song

Applyrx reads Apple Music state through AppleScript. On first run, macOS may ask
for Automation permission to control Music.app.

## Quick Start

```bash
git clone https://github.com/rakel/applyrx.git
cd applyrx
./scripts/bootstrap.sh
./run_lyricsx_ui.sh
```

Open Apple Music, play a song, and open the built-in lyrics panel once. Applyrx
will only use Apple Music TTML cache and will not fall back to third-party lyric
providers.

## Build The App

```bash
./scripts/build_app.sh
open dist/Applyrx.app
```

The current app build is a local development bundle. It is intended to run from
this project directory and may rely on the local `venv` and source helper files.
A fully standalone distributable app is a future packaging step.

## CLI

Print full current state as JSON:

```bash
./venv/bin/python applyrx_cli.py state
```

Print only the current lyric line:

```bash
./venv/bin/python applyrx_cli.py current-line
```

Print the current song lyrics as LRC:

```bash
./venv/bin/python applyrx_cli.py lyrics --format lrc
```

Watch lyric changes as JSON Lines:

```bash
./venv/bin/python applyrx_cli.py watch
```

All CLI commands accept `--offset`, where positive values show lyrics earlier:

```bash
./venv/bin/python applyrx_cli.py --offset 1.2 watch
```

## Configuration

Applyrx writes config to:

```text
~/.applyrx/config.json
```

Useful fields include:

- `offset`: lyric timing offset in seconds.
- `desktop_visible`: show or hide the floating desktop lyric.
- `menubar_lyrics_visible`: show current lyric text in the menu bar.
- `font_size_current`: main desktop lyric font size.
- `panel_width` and `panel_height`: desktop lyric background size.
- `background_visible`: show or hide the desktop lyric background.

Most of these can be changed from the menu bar app.

## Notes

- Applyrx currently supports Apple Music only.
- It does not use LRClib, NetEase, QQ Music, or other third-party lyric APIs by default.
- If no matching TTML cache exists, open the lyrics panel in Apple Music for the current song first.
- If a song still does not match, Applyrx will not guess, because avoiding wrong lyrics is a core rule.

## License

MIT
