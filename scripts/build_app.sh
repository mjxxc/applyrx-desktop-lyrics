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

# The native lyrics panel is Apple Silicon only and requires macOS 26 for
# NSGlassEffectView. Pin the deployment target so the build does not depend on
# the macOS version of the machine that happens to run this script.
MACOS_SDK="${MACOS_SDK:-/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk}"
APPLYRX_TARGET="${APPLYRX_TARGET:-arm64-apple-macos26.0}"

mkdir -p build
swiftc -parse-as-library -swift-version 5 \
  -target "$APPLYRX_TARGET" \
  -sdk "$MACOS_SDK" \
  native/ApplyrxLyricsPanel.swift \
  -framework AppKit \
  -framework Carbon \
  -framework SwiftUI \
  -o build/ApplyrxLyricsPanel

rm -rf dist
./venv/bin/python setup.py py2app

APP="dist/Applyrx.app"
PLIST="$APP/Contents/Info.plist"

# py2app records the interpreter that ran setup.py in PythonInfoDict, which
# leaks the absolute build path (developer user name and worktree location) into
# the shipped bundle. Nothing at runtime reads that key: the loader uses
# PyRuntimeLocations, which already points inside the bundle. Drop the field and
# re-seal the bundle, because editing Info.plist invalidates the previous seal.
if /usr/libexec/PlistBuddy -c "Print :PythonInfoDict:PythonExecutable" "$PLIST" >/dev/null 2>&1; then
  /usr/libexec/PlistBuddy -c "Delete :PythonInfoDict:PythonExecutable" "$PLIST"
  echo "Removed PythonInfoDict.PythonExecutable (build-machine interpreter path)."
fi

if grep -qF "$HOME" "$PLIST"; then
  echo "Refusing to finish: $PLIST still contains the build home directory ($HOME)." >&2
  exit 1
fi

codesign --force --sign - "$APP"
codesign --verify --strict "$APP"

echo "Built: $(pwd)/$APP"
echo "Version: $(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PLIST")"
echo "Minimum system: $(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$PLIST")"
echo "Native panel target: $(otool -l build/ApplyrxLyricsPanel | awk '/LC_BUILD_VERSION/{f=1} f&&/minos/{print $2; exit}') / $(file -b build/ApplyrxLyricsPanel)"
