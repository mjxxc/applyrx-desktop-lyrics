#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -x ./venv/bin/python ]]; then
  echo "venv not found. Run ./scripts/bootstrap.sh first." >&2
  exit 1
fi

pkill -f '/Applyrx.app/Contents/MacOS/Applyrx' 2>/dev/null || true
rm -rf build dist
./venv/bin/python setup.py py2app

echo "Built: $(pwd)/dist/Applyrx.app"
