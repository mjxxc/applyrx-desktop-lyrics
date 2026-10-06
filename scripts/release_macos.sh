#!/usr/bin/env bash
#
# Build and package the Applyrx macOS release asset.
#
# This script only produces local artifacts. It never runs git commit, git tag,
# git push, or any GitHub release step; publishing stays a manual decision.
#
# Usage:
#   ./scripts/release_macos.sh
#
# The script refuses to run unless the working tree is clean. For a local dry
# run before the release changes are committed, set RELEASE_ALLOW_DIRTY=1; the
# default stays strict so a published asset always comes from a committed tree.
#
# Output (repository root, ignored by git):
#   Applyrx-v<version>-macOS.zip
#   Applyrx-v<version>-macOS.zip.sha256
#
set -euo pipefail

cd "$(dirname "$0")/.."

APP="dist/Applyrx.app"
PLIST="$APP/Contents/Info.plist"
NATIVE_PANEL="$APP/Contents/Resources/native/ApplyrxLyricsPanel"
SMOKE_LOG="$(mktemp -t applyrx-smoke)"

fail() {
  echo "release check failed: $*" >&2
  exit 1
}

cleanup() {
  rm -f "$SMOKE_LOG"
}
trap cleanup EXIT

echo "== 1. worktree state =="
if [[ -n "$(git status --porcelain)" ]]; then
  if [[ "${RELEASE_ALLOW_DIRTY:-0}" == "1" ]]; then
    echo "WARNING: working tree is not clean; continuing because RELEASE_ALLOW_DIRTY=1" >&2
    git status --short >&2
  else
    git status --short >&2
    fail "working tree is not clean (set RELEASE_ALLOW_DIRTY=1 only for a local dry run)"
  fi
else
  echo "worktree clean"
fi

echo "== 2. version =="
SETUP_VERSION="$(sed -n 's/.*"CFBundleShortVersionString": "\([^"]*\)".*/\1/p' setup.py | head -1)"
SETUP_BUNDLE_VERSION="$(sed -n 's/.*"CFBundleVersion": "\([^"]*\)".*/\1/p' setup.py | head -1)"
[[ -n "$SETUP_VERSION" ]] || fail "cannot read CFBundleShortVersionString from setup.py"
[[ "$SETUP_VERSION" == "$SETUP_BUNDLE_VERSION" ]] || fail "setup.py version keys differ ($SETUP_VERSION vs $SETUP_BUNDLE_VERSION)"
grep -q "v$SETUP_VERSION" README.md || fail "README.md does not mention v$SETUP_VERSION"
grep -q "v$SETUP_VERSION" README.zh-CN.md || fail "README.zh-CN.md does not mention v$SETUP_VERSION"
echo "version $SETUP_VERSION (setup.py, README.md, README.zh-CN.md agree)"

echo "== 3. build =="
./scripts/build_app.sh

echo "== 4. bundle presence =="
[[ -d "$APP" ]] || fail "$APP was not produced"
[[ -f "$PLIST" ]] || fail "$PLIST is missing"
[[ -x "$NATIVE_PANEL" ]] || fail "$NATIVE_PANEL is missing"

echo "== 5. bundle metadata =="
PLIST_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PLIST")"
PLIST_BUNDLE_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$PLIST")"
PLIST_MIN_SYSTEM="$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$PLIST" 2>/dev/null || true)"
[[ "$PLIST_VERSION" == "$SETUP_VERSION" ]] || fail "CFBundleShortVersionString is $PLIST_VERSION, expected $SETUP_VERSION"
[[ "$PLIST_BUNDLE_VERSION" == "$SETUP_VERSION" ]] || fail "CFBundleVersion is $PLIST_BUNDLE_VERSION, expected $SETUP_VERSION"
[[ "$PLIST_MIN_SYSTEM" == "26.0" ]] || fail "LSMinimumSystemVersion is '$PLIST_MIN_SYSTEM', expected 26.0"
echo "CFBundleShortVersionString=$PLIST_VERSION CFBundleVersion=$PLIST_BUNDLE_VERSION LSMinimumSystemVersion=$PLIST_MIN_SYSTEM"

echo "== 6. architecture =="
MAIN_ARCHS="$(lipo -archs "$APP/Contents/MacOS/Applyrx")"
PANEL_ARCHS="$(lipo -archs "$NATIVE_PANEL")"
[[ "$MAIN_ARCHS" == *arm64* ]] || fail "app executable has no arm64 slice ($MAIN_ARCHS)"
[[ "$PANEL_ARCHS" == "arm64" ]] || fail "native lyrics panel is not arm64-only ($PANEL_ARCHS)"
PANEL_MINOS="$(otool -l "$NATIVE_PANEL" | awk '/LC_BUILD_VERSION/{f=1} f&&/minos/{print $2; exit}')"
[[ "$PANEL_MINOS" == "26.0" ]] || fail "native lyrics panel minos is $PANEL_MINOS, expected 26.0"
echo "app executable: $MAIN_ARCHS | native panel: $PANEL_ARCHS (minos $PANEL_MINOS)"

echo "== 7. privacy =="
if grep -qF "$HOME" "$PLIST"; then
  grep -nF "$HOME" "$PLIST" >&2
  fail "Info.plist leaks the build home directory"
fi
if grep -q "/Users/" "$PLIST"; then
  grep -n "/Users/" "$PLIST" >&2
  fail "Info.plist contains an absolute /Users/ path"
fi
for pattern in "Cache.db" ".applyrx" "config.json" "*.log"; do
  found="$(find "$APP" -name "$pattern" 2>/dev/null || true)"
  [[ -z "$found" ]] || { echo "$found" >&2; fail "bundle contains $pattern"; }
done
echo "no build-home path in Info.plist, no Cache.db / .applyrx / user config / logs in the bundle"

echo "== 8. smoke test =="
SELF_TEST_JSON="$("$NATIVE_PANEL" --self-test 2>/dev/null | tail -1)"
[[ -n "$SELF_TEST_JSON" ]] || fail "bundled native panel produced no self-test output"
SELF_TEST_RESULT="$(./venv/bin/python - "$SELF_TEST_JSON" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
failed = {k: v for k, v in data.items() if v is not True}
if failed:
    print("failed: " + ", ".join(sorted(failed)), file=sys.stderr)
    raise SystemExit(1)
# py2app strips local symbols from the packaged panel, so assert on the v0.2.0
# self-test keys instead of nm output: they only exist in this implementation.
markers = (
    "targetLyricBecomesCurrentWithoutStyleJump",
    "threeLineOutgoingLyricSettlesIntoContext",
    "identityCardFitsLyricViewport",
    "panelHeightCoversRenderedLineBoxes",
    "trackChangeDoesNotAnimate",
    "lyricIndexIsDecoded",
)
missing = [name for name in markers if name not in data]
if missing:
    print("packaged panel is missing v0.2.0 checks: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
if len(data) < 53:
    print(f"expected at least 53 self-test checks, found {len(data)}", file=sys.stderr)
    raise SystemExit(1)
print(f"{sum(1 for v in data.values() if v is True)}/{len(data)}")
PY
)" || fail "bundled native panel self-test did not pass"
echo "bundled native panel self-test: $SELF_TEST_RESULT"

"$APP/Contents/MacOS/Applyrx" >"$SMOKE_LOG" 2>&1 &
APP_PID=$!
sleep 6
if ! kill -0 "$APP_PID" 2>/dev/null; then
  echo "--- app output ---" >&2
  cat "$SMOKE_LOG" >&2
  fail "dist/Applyrx.app exited during the launch smoke test"
fi
echo "dist/Applyrx.app launched and stayed running (pid $APP_PID)"
kill "$APP_PID" 2>/dev/null || true
wait "$APP_PID" 2>/dev/null || true
pkill -f "dist/Applyrx.app/Contents/Resources/native/ApplyrxLyricsPanel" 2>/dev/null || true

echo "== 9. package =="
VERSION="$SETUP_VERSION"
ZIP="Applyrx-v${VERSION}-macOS.zip"
SHA="${ZIP}.sha256"
rm -f "$ZIP" "$SHA"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
shasum -a 256 "$ZIP" > "$SHA"

ZIP_BYTES="$(stat -f%z "$ZIP")"
ZIP_HUMAN="$(du -h "$ZIP" | cut -f1)"
SHA_VALUE="$(awk '{print $1}' "$SHA")"

echo
echo "== done =="
echo "artifact:    $(pwd)/$ZIP"
echo "size:        $ZIP_BYTES bytes ($ZIP_HUMAN)"
echo "sha-256:     $SHA_VALUE"
echo "checksum:    $(pwd)/$SHA"
echo
echo "Next steps are manual: review the artifact, then push, tag, and publish."
