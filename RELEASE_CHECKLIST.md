# Release Checklist

Use this before publishing Applyrx to GitHub.

1. Verify Apple Music is running and the lyrics panel has been opened for the current song.
2. Run static checks:

```bash
./venv/bin/python -m py_compile applyrx_ui.py lyrics_state.py applyrx_cli.py applyrx_state.py main.py apple_music_ttml.py setup.py
```

3. Verify CLI:

```bash
./venv/bin/python applyrx_cli.py state
./venv/bin/python applyrx_cli.py current-line
./venv/bin/python applyrx_cli.py lyrics --format lrc
```

4. Verify GUI:

```bash
./run_applyrx.sh
```

5. Verify app build:

```bash
./scripts/build_app.sh
open dist/Applyrx.app
```

6. Confirm these paths are not committed:

```text
venv/
build/
dist/
__pycache__/
.DS_Store
```

7. Update the GitHub URL in `README.md` after creating the repository.
