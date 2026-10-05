# macOS Manual Acceptance

The following checks are manual because they involve global keyboard input and
Apple Music playback. Applyrx reads the current Music state; do not use Applyrx
to control playback during these checks.

## Panel hotkeys and click-through

1. Launch Applyrx and confirm the floating lyrics panel is visible.
2. Press **Control-Option-Command-L**. Confirm the panel hides; press it again
   and confirm the panel returns.
3. Press **Control-Option-Command-M**. Confirm the panel enters temporary drag
   mode, then drag it to a convenient location.
4. Wait 10 seconds without pressing another hotkey. Confirm the panel reports
   that it has left drag mode. Move or click over the panel and confirm the
   underlying app receives input; this verifies click-through was restored.

The shortcuts are registered with Carbon `RegisterEventHotKey`; this code does
not install an Accessibility event tap or use System Events to monitor keys.
No Accessibility permission change is part of this acceptance procedure.

## Playback synchronization

With a song already playing in Music, keep Music visible and compare its play
position with the lyric panel:

1. **Playing:** confirm the displayed position/current lyric follows Music and
   that a new current line appears as its timestamp is reached.
2. **Pause:** pause from Music. Confirm the shown position and current line
   freeze while Music is paused.
3. **Resume:** resume from Music. Confirm the panel follows Music's actual
   position and progresses to the next timestamped line.
4. **Seek forward:** seek forward in Music. Confirm the lyric changes
   immediately to the line for the new position rather than advancing from the
   old line.
5. **Seek backward:** seek backward in Music. Confirm the current lyric
   relocates to the earlier timestamp.
6. **Switch track:** choose another track in Music. Confirm the previous
   track's lyrics disappear immediately, the panel shows a loading/no-match
   state, and only lyrics validated for the new track appear.

Do not treat an absent local cache match as proof that the Apple Music catalog
has no lyrics. Record the observed Music position, panel current line, and
whether the result was matched or unavailable for each step.

## Build environment

Run `./scripts/bootstrap.sh` and `./scripts/build_app.sh`. Both require Python
3.10 or newer; they use `python3` when compatible or select an installed
`python3.13` through `python3.10`. To choose a specific executable, set
`APPLYRX_PYTHON=/path/to/python3.12`. If no compatible interpreter is found,
the scripts print an installation recommendation and leave system Python and
any existing incompatible `venv` unchanged.
