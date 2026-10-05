#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

source scripts/select_python.sh
PYTHON="$(applyrx_select_python)"

if [[ -x ./venv/bin/python ]]; then
  if ! ./venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    echo "The existing venv uses Python older than 3.10; it was left unchanged." >&2
    echo "Move or remove ./venv yourself, then rerun scripts/bootstrap.sh." >&2
    exit 1
  fi
else
  "$PYTHON" -m venv venv
fi
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt

echo "Ready. Try: ./run_applyrx.sh"
