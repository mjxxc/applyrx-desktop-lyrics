# Applyrx

Applyrx is an unofficial macOS desktop lyrics overlay for Apple Music. It reads
timed lyrics from Apple Music's local cache and displays them in a synchronized,
click-through desktop panel with menu bar integration.

This project is based on and inspired by the MIT-licensed
[Applyrx](https://github.com/rakei076/applyrx) project. It is an independent,
unofficial third-party tool and is not affiliated with, endorsed by, or
sponsored by Apple.

## Demo

![Applyrx desktop lyrics demo](./assets/applyrx-demo.gif)

[Download the silent MP4 demo](./assets/applyrx-demo.mp4).

## Features

- Synchronized Apple Music lyrics in a desktop overlay.
- Menu bar integration and a native SwiftUI lyrics panel.
- Reads catalog/song responses and `syllable-lyrics` TTML from the local Apple
  Music `Cache.db` and `fsCachedData` cache.
- Parses TTML into timed lyric lines and highlights words when Apple supplies
  word timing.
- Native Settings window for lyric sizes, context opacity, displayed line count,
  background opacity, and floating-panel position restoration.
- Strict track matching: incomplete or conflicting metadata is not guessed.
- Retries a local cache lookup after a track initially has no match.
- Follows Music playback position, including pause, resume, and seeking.
- Click-through overlay with a temporary drag mode that restores click-through.
- Global shortcuts: `Control-Option-Command-L` toggles the desktop lyrics panel;
  `Control-Option-Command-M` temporarily enables drag mode.

## Requirements

- macOS with Apple Music.app.
- Python 3.10 or newer for development and building; the v0.1.0 release build
  was produced with Python 3.12.
- Apple Music must have cached lyrics for the song. Opening the built-in lyrics
  panel in Music may be necessary to populate local cache data.
- macOS Automation permission for reading the current Apple Music track.

## Installation

Clone the repository and install its Python dependencies in a project-local
virtual environment:

```bash
git clone https://github.com/<owner>/applyrx-desktop-lyrics.git
cd applyrx-desktop-lyrics
./scripts/bootstrap.sh
```

To run the development app:

```bash
./run_applyrx.sh
```

The app appears as a menu bar item and a desktop lyrics overlay. Play a track
in Music.app; the app reads its metadata and searches only the local Apple Music
cache for a strictly matching lyric response.

## Build

Build a macOS app bundle with:

```bash
./scripts/build_app.sh
open dist/Applyrx.app
```

The build compiles `native/ApplyrxLyricsPanel.swift` and packages the app under
`dist/Applyrx.app`. The build scripts use an installed compatible Python
interpreter and do not modify system Python. Set `APPLYRX_PYTHON` to select a
specific interpreter when needed.

## Usage

1. Start Applyrx and allow its macOS Automation request to read Music.app.
2. Play a song in Apple Music.
3. If lyrics are not found yet, open that song's built-in lyrics view in Music
   and give the local cache time to update. Applyrx retries local lookup for the
   current track.
4. Use the menu bar controls to show or hide the panel and configure its
   appearance. Choose **Settings…** for live lyric and window preferences.

Lyrics may remain unavailable if the data is not cached, a different song
version is cached, metadata is incomplete/conflicting, or strict matching
cannot establish that the cached lyrics belong to the current track.

## Keyboard Shortcuts

| Shortcut | Action |
| --- | --- |
| `Control-Option-Command-L` | Show or hide the desktop lyrics overlay |
| `Control-Option-Command-M` | Temporarily enable drag mode; click-through is restored automatically |

## Architecture

```text
Apple Music.app
      │ read current track metadata and playback position
      ▼
CurrentTrackManager ──▶ AppleMusicCacheProvider
                              │ read-only
                              ▼
                 Cache.db + fsCachedData
                              │
                 catalog/song cache response
                              │ strict identity and metadata matching
                              ▼
             syllable-lyrics TTML ──▶ TTMLParser
                                           │ timed lines / available word timing
                                           ▼
                                     LyricsEngine
                                           │
                             Python panel bridge (JSON over stdin)
                                           ▼
                       SwiftUI desktop overlay + Settings
```

The overlay uses Music's reported playback position as its synchronization
reference. Lyrics are sourced from cached catalog/song responses containing
Apple Music's `syllable-lyrics` extension; Applyrx does not ask a third-party
lyrics service to identify or provide lyrics.

## Limitations

- Apple Music does not provide ordinary third-party developers with a public,
  general-purpose interface to its complete timed lyric catalog. This project
  depends on Apple Music's local cache format, an implementation detail that
  can change with Apple Music or macOS updates.
- A lyric response must already be present in the local cache. Not every track,
  storefront, or song version will be available.
- Strict matching intentionally refuses to display lyrics when identity or
  metadata is insufficient, incomplete, or conflicting.
- Word-level highlighting and additional context lines depend on the timing and
  cached lyrics Apple Music supplies.
- This is a macOS/Apple Music project; other players and operating systems are
  not supported.
- The release app is not notarized.

## Privacy

- The desktop lyrics provider reads Apple Music's `Cache.db` in read-only mode
  and reads associated `fsCachedData` response bodies. It does not modify the
  Apple Music cache.
- Applyrx reads current track metadata and playback position from Music.app. It
  does not issue playback controls.
- Applyrx does not read an Apple ID password or authentication credentials.
- The desktop lyrics lookup does not upload track information or lyric content
  to third-party lyrics services.
- Presentation preferences and an optional panel position are stored in the
  macOS user defaults domain `com.applyrx.desktoplyrics`. Legacy app preferences
  remain under `~/.applyrx`; neither location stores Apple Music credentials.

## Troubleshooting

**The overlay says no matching lyrics.** Confirm Music.app is playing the
intended track, then open that track's built-in lyrics view so Music can
populate its local cache. Applyrx will retry. It will continue to refuse a
candidate if strict identity checks cannot establish a safe match.

**The app cannot read the current track.** Check macOS Privacy & Security →
Automation and allow Applyrx to access Music.app. Restart Applyrx after changing
the permission.

**The panel does not appear.** Check the Applyrx menu bar item and toggle
desktop lyrics with `Control-Option-Command-L`. Confirm macOS permits the
application to run.

**The overlay cannot be moved.** Use `Control-Option-Command-M` to enter
temporary drag mode. Click-through is restored automatically after the drag
window.

## Roadmap

- Improve release distribution and signing/notarization.
- Improve cache compatibility diagnostics while preserving strict matching.

## Contributing

Issues and pull requests are welcome. Please avoid including Apple Music cache
files, personal configuration, credentials, or logs containing private data in
bug reports. Changes must preserve read-only cache access, local lyric lookup,
and strict track matching.

## License

Applyrx is distributed under the MIT License. See [LICENSE](./LICENSE) for the
original copyright and license text.

## Disclaimer

Applyrx is an unofficial third-party project. Apple Music, Apple, and macOS are
trademarks of Apple Inc. This project is not affiliated with or endorsed by
Apple. Compatibility with Apple Music and macOS is not guaranteed and may
change as those products evolve.
