#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

source scripts/select_python.sh
BUILD_PYTHON="$(applyrx_select_python)"

if [[ ! -x ./venv/bin/python ]]; then
  echo "venv not found. Creating it with $("$BUILD_PYTHON" --version) via scripts/bootstrap.sh."
  ./scripts/bootstrap.sh
fi

if ! ./venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "The existing venv uses Python older than 3.10; the app was not built." >&2
  echo "Move or remove ./venv yourself, then rerun scripts/bootstrap.sh." >&2
  exit 1
fi

mkdir -p build
MACOS_SDK="${MACOS_SDK:-/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk}"
swiftc -parse-as-library -swift-version 5 -sdk "$MACOS_SDK" \
  native/ApplyrxLyricsPanel.swift \
  -framework AppKit \
  -framework Carbon \
  -framework SwiftUI \
  -o build/ApplyrxLyricsPanel
rm -rf dist
./venv/bin/python setup.py py2app

echo "Built: $(pwd)/dist/Applyrx.app"
