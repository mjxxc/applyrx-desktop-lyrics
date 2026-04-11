#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -x ./venv/bin/python ]]; then
  exec ./venv/bin/python ./lyricsx_style_app.py "$@"
fi
exec python3 ./lyricsx_style_app.py "$@"
