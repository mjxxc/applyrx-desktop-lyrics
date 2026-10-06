# Release Checklist — Applyrx v0.2.0

Use this before publishing Applyrx v0.2.0 to GitHub. Items are manual unless a
command is given.

## Source

- [ ] Working tree is clean: `git status` reports nothing to commit.
- [ ] Version is `0.2.0` in `setup.py` (`CFBundleVersion` and
      `CFBundleShortVersionString`).
- [ ] No stale `0.1.0` references remain in source, `README.md`, or
      `README.zh-CN.md`:
      `git grep -n "0\.1\.0" -- . | grep -v tests/fixtures`
- [ ] `README.md` and `README.zh-CN.md` both mention `v0.2.0`.

## Tests

- [ ] Swift self-test passes 53/53:
      `./build/ApplyrxLyricsPanel --self-test`
- [ ] Python suite passes 120/120:
      `./venv/bin/python -m unittest tests.test_build_environment tests.test_lyrics_panel tests.test_lyrics_provider tests.test_lyrics_sync tests.test_music_cache_diagnostic tests.test_no_fixed_home_path tests.test_word_timing_diagnostic`
- [ ] `git diff --check` prints nothing.

> `tests/` has no `__init__.py`, so `python -m unittest discover` is not
> available. Use the explicit module list above.

## Build

- [ ] Built with the macOS 26.5 Command Line Tools SDK
      (`/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`, override with
      `MACOS_SDK`).
- [ ] Native lyrics panel is `arm64`.
- [ ] Native lyrics panel built with `-target arm64-apple-macos26.0`
      (`LC_BUILD_VERSION` `minos 26.0`).
- [ ] `LSMinimumSystemVersion` is `26.0` in the built `Info.plist`.
- [ ] `./scripts/build_app.sh` completes and re-seals the app bundle.

## Privacy

- [ ] No `Cache.db` inside `dist/Applyrx.app`.
- [ ] No user local config (`.applyrx`, `config.json`) inside the app bundle.
- [ ] No token, credential, cookie, or Apple ID data in the repository or bundle.
- [ ] No build-machine path in the published app `Info.plist`:
      `grep -F "$HOME" dist/Applyrx.app/Contents/Info.plist` returns nothing, and
      `grep "/Users/" dist/Applyrx.app/Contents/Info.plist` returns nothing.
      (`build_app.sh` removes py2app's `PythonInfoDict.PythonExecutable`, which
      contains the build interpreter's absolute path.)
- [ ] No log files in the bundle.

## Manual acceptance (real Apple Music)

- [ ] Apple Music playback: panel appears and follows the current track.
- [ ] Prelude: song title + artist are shown before the first lyric line.
- [ ] 2-line default: current line + next line.
- [ ] Continuous scrolling: the track moves up smoothly, with no ghosting, no
      fade, and no jump when the scroll finishes.
- [ ] Word-level highlighting follows playback (and line-level fallback still
      works for lyrics without word timing).
- [ ] Pause / resume: the panel freezes on pause and does not scroll extra on
      resume.
- [ ] Seek: the panel follows the new position.
- [ ] Large seek: the panel relocates directly instead of scrolling line by line.
- [ ] Track change: the panel switches immediately, with no old-song scroll.
- [ ] Click-through: clicks pass through the panel.
- [ ] Shortcuts: `Control-Option-Command-L` toggles the panel;
      `Control-Option-Command-M` enables temporary drag mode.
- [ ] Settings window opens and edits apply live (lyric sizes, context opacity,
      1/2/3 display lines, glass tint).
- [ ] Position persistence: the panel position is restored after relaunch.
- [ ] Liquid Glass: the panel renders as a native `NSGlassEffectView` (clear).
- [ ] No-lyrics state: title/artist identity card is shown when no lyrics match.
- [ ] 1-line and 3-line modes render without clipping any glyph.

## Artifact

- [ ] `dist/Applyrx.app` rebuilt from the current commit (not reused).
- [ ] Version verified as `0.2.0` in the built `Info.plist`.
- [ ] The app launches:
      `open dist/Applyrx.app`
- [ ] `./scripts/release_macos.sh` completes with exit code 0.
- [ ] `Applyrx-v0.2.0-macOS.zip` created.
- [ ] `Applyrx-v0.2.0-macOS.zip.sha256` created.
- [ ] Zip inspected (`unzip -l`): contains `Applyrx.app` with its resources; no
      `.git`, no build cache, no test logs, no user data.
- [ ] No developer path leakage in the packaged app `Info.plist`.
- [ ] Zip re-checksummed against the published file:
      `shasum -a 256 -c Applyrx-v0.2.0-macOS.zip.sha256`

## GitHub

- [ ] Push the release branch.
- [ ] Create tag `v0.2.0` on the release commit.
- [ ] Create the GitHub Release `v0.2.0`.
- [ ] Upload `Applyrx-v0.2.0-macOS.zip`.
- [ ] Upload `Applyrx-v0.2.0-macOS.zip.sha256`.
- [ ] Paste the Release Notes (title: *Applyrx v0.2.0 — Native Liquid Glass
      Lyrics Experience*), including the macOS 26+ requirement and the
      unsigned / not notarized status.
