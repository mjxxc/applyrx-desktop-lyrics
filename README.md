# Applyrx

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <strong>Native, frame-accurate Apple Music lyrics for macOS.</strong><br>
  Desktop overlay · Menu bar · Full-lyrics window · CLI
</p>

---

Applyrx is a macOS live-lyrics application for Apple Music, inspired by the
desktop-overlay experience popularized by [LyricsX](https://github.com/ddddxxx/LyricsX).
Instead of querying third-party providers, Applyrx reads Apple Music's own
signed TTML lyric responses directly from the local `NSURLCache` — so the
lyrics are byte-for-byte identical to what the Apple Music app itself renders,
including official translations, romaji, and word-level timing where Apple
provides it.

Applyrx never guesses. If the currently playing track cannot be unambiguously
matched against a cached TTML entry by Apple catalog id, title, artist, and
duration, Applyrx surfaces an explicit error instead of displaying lyrics from
the wrong song.

## Demo

![Applyrx desktop lyric demo](./assets/applyrx-demo.gif)

[Download the silent MP4 demo](./assets/applyrx-demo.mp4).

## How It Works

```
Apple Music.app ──plays──▶ signed request to /ttmlLyrics ──▶ NSURLCache (Cache.db)
                                                                    │
                                                                    ▼
                                        ┌───────────────────────────────────┐
                                        │ Applyrx                           │
                                        │                                   │
    AppleScript (player state) ────────▶│  1. read current track metadata   │
                                        │  2. resolve adam_id via iTunes    │
                                        │  3. locate matching TTML cache    │
                                        │  4. replay signed request (curl)  │
                                        │  5. parse TTML → timed lines      │
                                        │  6. render UI / emit CLI events   │
                                        └───────────────────────────────────┘
                                                         │
                         ┌───────────────────────────────┼───────────────────────────────┐
                         ▼                               ▼                               ▼
                Desktop overlay                   Menu bar lyric                       CLI
```

No private entitlements, no Accessibility hacks, no re-implementation of
Apple's signing. Applyrx simply reuses the signed request that Music.app has
already cached and replays it with the same headers.

## Features

- **Apple Music native lyrics** from the local TTML cache — same source as the
  in-app lyrics panel.
- **Desktop overlay** that is click-through by default, with temporary drag
  unlock, adjustable size, and optional background.
- **Menu bar lyric** showing the current line; can be toggled off.
- **Full-lyrics window** with current-line highlighting and smooth scrolling.
- **CLI** (`applyrx_cli.py`) with `state`, `current-line`, `lyrics`, and
  `watch` subcommands, all JSON-friendly for scripting and integrations.
- **Strict match policy**: catalog id + title + artist + duration must agree.
  Traditional/simplified Chinese is normalized, and a unique-duration fallback
  handles storefront title mismatches.
- **Multi-storefront lookup** (CN/TW/US) so Apple Music CN catalog ids resolve
  correctly.
- **Offline after first play**: once a song's TTML is cached by Apple Music,
  Applyrx needs no network access.
- **Local JSON config** at `~/.applyrx/config.json`, editable from the menu
  bar.

## Requirements

- macOS with Apple Music.app
- Python 3.11 or newer
- The in-app lyrics panel opened at least once for the current song, so
  Apple Music populates its TTML cache
- Automation permission for Music.app (macOS will prompt on first run)

## Quick Start

```bash
git clone https://github.com/rakei076/applyrx.git
cd applyrx
./scripts/bootstrap.sh
./run_applyrx.sh
```

Play a song in Apple Music, open the built-in lyrics panel once, and Applyrx
will pick up the TTML cache and start rendering synchronized lyrics.

## Build as an App Bundle

```bash
./scripts/build_app.sh
open dist/Applyrx.app
```

The bundle currently expects to run from the project directory and uses the
local `venv`. A fully standalone, notarized distributable is planned; see
[`RELEASE_CHECKLIST.md`](./RELEASE_CHECKLIST.md).

## CLI

Applyrx ships a scriptable CLI that reuses the exact same matching logic as
the GUI.

```bash
# Print full current state as JSON
./venv/bin/python applyrx_cli.py state

# Print only the current lyric line
./venv/bin/python applyrx_cli.py current-line

# Dump current song lyrics as LRC
./venv/bin/python applyrx_cli.py lyrics --format lrc

# Stream lyric changes as JSON Lines
./venv/bin/python applyrx_cli.py watch

# Apply a global offset in seconds (positive = earlier)
./venv/bin/python applyrx_cli.py --offset 1.2 watch
```

The `watch` subcommand works well with Raycast, Alfred, BTT, tmux status
lines, and Stream Deck integrations.

## Configuration

Applyrx stores its config at:

```
~/.applyrx/config.json
```

| Field | Description |
|---|---|
| `offset` | Lyric timing offset in seconds |
| `desktop_visible` | Show or hide the floating desktop lyric |
| `desktop_click_through` | Let mouse clicks pass through the desktop lyric |
| `menubar_lyrics_visible` | Show the current lyric in the menu bar |
| `font_size_current` | Font size of the main desktop lyric line |
| `panel_width` / `panel_height` | Size of the desktop lyric panel |
| `background_visible` | Show or hide the desktop lyric background |

Most of these can be changed from the menu bar without editing JSON.

## Project Layout

```
applyrx/
├── apple_music_ttml.py      # NSURLCache reader, request replay, TTML parser
├── main.py                  # Strict matching pipeline (adam_id + metadata)
├── applyrx_ui.py     # Desktop overlay + menu bar GUI
├── applyrx_cli.py           # Scriptable CLI
├── applyrx_state.py         # Shared runtime state
├── lyrics_state.py          # Lyric progression / current-line tracking
├── scripts/                 # bootstrap.sh, build_app.sh
├── run_applyrx.sh        # GUI entry point
└── dist/Applyrx.app         # Local app bundle
```

## Design Principles

1. **Never display wrong lyrics.** A clear error beats a confident mismatch.
2. **Apple is the source of truth.** No third-party providers in the default
   path.
3. **Reuse, don't re-sign.** Applyrx never attempts to reproduce Apple's
   request signing; it replays what Music.app has already signed.
4. **Scriptable by default.** Every piece of state the GUI shows is also
   available through the CLI as JSON.
5. **Offline after first play.** Once a song's TTML is cached, Applyrx works
   without any network round-trips.

## FAQ

**Does Applyrx need my Apple ID or any API key?**
No. It only reads the local Apple Music cache and replays already-signed
requests.

**Does Applyrx modify anything in Apple Music?**
No. It opens a read-only copy of the cache database and never writes to
Music.app's state.

**Why does my song show "no matching lyrics"?**
Open the lyrics panel for that song once in Apple Music so the TTML cache is
populated, then Applyrx will pick it up automatically.

**Can I use this on Spotify / YouTube Music / NetEase?**
Not by design. Applyrx is intentionally Apple Music-only and Apple-TTML-only.

**How is matching made reliable across storefronts?**
Applyrx resolves the Apple catalog id via `itunes.apple.com/lookup`, iterating
through CN, TW, and US storefronts, then compares title, artist, and duration
against the player state. Traditional and simplified Chinese titles are
normalized before comparison.

## Roadmap

- Standalone notarized `.app` bundle
- Homebrew cask
- Optional word-level (karaoke) rendering where Apple provides it
- Native Swift menu bar host for lower idle CPU

## Related Projects

These projects share a similar goal and are worth knowing:

- [LyricsX](https://github.com/ddddxxx/LyricsX) — the original macOS desktop
  lyrics app, written in Swift, supports multiple music players and lyric
  providers.
- [sptlrx](https://github.com/raitonoberu/sptlrx) — terminal lyrics viewer
  for Spotify with a beautiful TUI.
- [LyricFever](https://www.lyricfever.com/) — macOS lyrics menubar app with
  Apple Music support.

## Contributing

Issues and pull requests are welcome. Please keep the strict-matching
invariant: any change that can cause Applyrx to display lyrics from a
different song than the one currently playing will be rejected.

For questions, bug reports, or ideas, please open a GitHub Issue/PR or reach
out on X: [@LuJia32473](https://x.com/LuJia32473).

## License

[MIT](./LICENSE)
