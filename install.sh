#!/bin/sh
# Embabel Me — one-command install.
#
#   curl -fsSL https://get.embabel.com/me | sh
#
# This script is short and boring ON PURPOSE. You are being asked to pipe a
# remote script into a shell, which is a thing you should be suspicious of, so
# everything it does should fit on one screen and read plainly:
#
#   1. check Docker is installed and running
#   2. download this repo into ~/embabel-me
#   3. hand off to ./me.py, which owns the actual setup
#
# It installs nothing globally, needs no root, and writes only inside that one
# directory. To undo it: `docker compose down -v` in there, then delete it.
#
# Environment:
#   EMBABEL_HOME   where to install         (default: ~/embabel-me)
#   EMBABEL_REF    branch or tag to fetch   (default: main)
#   EMBABEL_DOOR   me | worlds              (default: me)

set -eu

REPO="${EMBABEL_REPO:-embabel/appliance}"
# TODO: default to the latest RELEASE TAG once the appliance cuts them; a branch
# means you get whatever landed this morning, which is not what an installer
# should hand a new user.
REF="${EMBABEL_REF:-main}"
DOOR="${EMBABEL_DOOR:-me}"
HOME_DIR="${EMBABEL_HOME:-$HOME/embabel-me}"

say() { printf '  %s\n' "$*"; }
die() { printf '\n  %s\n\n' "$*" >&2; exit 1; }

printf '\n  Embabel Me — your own assistant, on your own machine\n\n'

# --- 1. Docker ------------------------------------------------------------
# The one prerequisite we cannot install for you, and the one worth failing
# early and clearly on: everything below is pointless without it.
command -v docker >/dev/null 2>&1 || die "Docker is required: https://docs.docker.com/get-started/get-docker/"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required — update Docker Desktop, or install the compose plugin."
docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start Docker Desktop, then run this again."

# Embeddings run locally, so the appliance needs Model Runner. A warning, not a
# failure: setup says the same thing with more room, and being told to fix two
# things at once by a script that then exits is a bad first minute.
if ! docker model status >/dev/null 2>&1; then
  say "Note: Docker Model Runner looks disabled — embeddings need it."
  say "      Enable it in Docker Desktop (Settings → AI), or: docker desktop enable model-runner"
  echo
fi

# --- 2. Download ----------------------------------------------------------
if [ -e "$HOME_DIR" ] && [ ! -f "$HOME_DIR/setup.py" ] && [ -n "$(ls -A "$HOME_DIR" 2>/dev/null || true)" ]; then
  die "$HOME_DIR exists and is not an Embabel install. Move it, or set EMBABEL_HOME."
fi

if [ -f "$HOME_DIR/setup.py" ]; then
  say "Updating ${HOME_DIR} (your .env and data are untouched)…"
else
  say "Installing into ${HOME_DIR}…"
fi

mkdir -p "$HOME_DIR"

# A tarball, not a file list: nothing here has to be kept in step with the repo
# layout, and the API form takes a branch, a tag or a commit alike.
#
# Downloaded to a FILE first, not piped straight into tar. In a pipeline the
# exit status is tar's, so a failed download reaches tar as an empty stream and
# the script cheerfully reports success over an empty directory — which is
# exactly what the first version of this script did.
TARBALL="$(mktemp)"
trap 'rm -f "$TARBALL"' EXIT INT TERM

AUTH=""
[ -n "${EMBABEL_TOKEN:-}" ] && AUTH="Authorization: Bearer ${EMBABEL_TOKEN}"

if [ -n "$AUTH" ]; then
  curl -fsSL -H "$AUTH" -o "$TARBALL" "https://api.github.com/repos/$REPO/tarball/$REF" \
    || die "Download failed. Check EMBABEL_TOKEN, your connection, and that '$REF' exists."
else
  curl -fsSL -o "$TARBALL" "https://api.github.com/repos/$REPO/tarball/$REF" \
    || die "Download failed. Check your connection, and that '$REF' exists in $REPO."
fi

tar xzf "$TARBALL" -C "$HOME_DIR" --strip-components=1 || die "Could not unpack the download."

# Post-condition, because a download that returns the wrong thing is worse than
# one that fails: prove the install is actually here before promising setup.
[ -f "$HOME_DIR/setup.py" ] || die "The download did not contain an Embabel appliance. Nothing was installed."

chmod +x "$HOME_DIR/me.py" "$HOME_DIR/worlds.py" "$HOME_DIR/setup.py" 2>/dev/null || true

# --- 3. Hand off ----------------------------------------------------------
# setup.py owns the real flow — starting the door, streaming the first boot,
# the account and model-provider key, and the offer to open the Me app. There
# is deliberately no second implementation of any of that here.
command -v python3 >/dev/null 2>&1 || die "python3 is required (it ships with macOS; on Linux: apt install python3)."

say "Done. Starting setup…"
echo
cd "$HOME_DIR"
if [ "$DOOR" = "worlds" ]; then
  exec python3 ./worlds.py
else
  exec python3 ./me.py
fi
