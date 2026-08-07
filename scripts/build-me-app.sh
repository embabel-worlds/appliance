#!/bin/sh
# Build the Me app into a real macOS application.
#
#   scripts/build-me-app.sh            # Embabel Me.app, for running locally
#   scripts/build-me-app.sh --dmg      # …and a DMG, for handing to someone else
#
# Why this exists at all: unpackaged, the app runs inside Electron's own bundle,
# so macOS names ELECTRON in permission prompts and in the app menu. Packaged, it
# has its own identity (com.embabel.me) and the prompt reads "Embabel Me wants to
# control Google Chrome" — the only version of that sentence a user can act on.
#
# Signing is ad-hoc: no Apple Developer membership needed, and enough for a
# stable TCC identity across launches of the same build. Rebuilding changes the
# code hash, so macOS asks for Automation permission again — clear the slate with
#     tccutil reset AppleEvents com.embabel.me
# Handing the DMG to anyone else needs Developer ID signing and notarization, set
# in me-app/package.json under build.mac.identity.

set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$ROOT/me-app"

DMG=false
[ "${1:-}" = "--dmg" ] && DMG=true

command -v npm >/dev/null 2>&1 || { echo "npm is required: https://nodejs.org" >&2; exit 1; }
[ "$(uname)" = "Darwin" ] || { echo "The Me app is macOS-only today — see me-app/src/platform/." >&2; exit 1; }

cd "$APP_DIR"

# Electron and electron-builder are dev-time only; the app itself has no runtime
# dependencies, which is why a packaged build stays small.
[ -d node_modules/electron ] || { echo "  Installing build dependencies…"; npm install; }

if $DMG; then
  echo "  Building Embabel Me.app and a DMG…"
  npx electron-builder --mac
else
  echo "  Building Embabel Me.app…"
  npx electron-builder --mac --dir
fi

APP="$(find release -maxdepth 2 -name 'Embabel Me.app' | head -1 || true)"
[ -n "$APP" ] || { echo "Build reported success but produced no app." >&2; exit 1; }

echo
echo "  Built: $APP_DIR/$APP"
echo "  Run it:      open -a \"$APP_DIR/$APP\""
echo "  Install it:  cp -R \"$APP_DIR/$APP\" /Applications/"
echo
echo "  ./me.py opens the packaged app when it finds one, in /Applications or here."
