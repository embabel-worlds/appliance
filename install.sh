#!/bin/sh
# Embabel Me — one-command install.
#
#   curl -fsSL https://raw.githubusercontent.com/embabel-worlds/appliance/main/install.sh | sh
#
# This script is short and boring ON PURPOSE. You are being asked to pipe a
# remote script into a shell, which is a thing you should be suspicious of, so
# everything it does should fit on one screen and read plainly:
#
#   1. check Docker is installed and running
#   2. download this repo into ~/embabel-worlds
#   3. hand off to ./me.py, which owns the actual setup
#
# It installs nothing globally, needs no root, and writes only inside that one
# directory. To undo it: `docker compose down -v` in there, then delete it.
#
# Environment:
#   EMBABEL_HOME   where to install         (default: ~/embabel-worlds)
#   EMBABEL_REF    branch or tag to fetch   (default: main)
#   EMBABEL_MODE   me | worlds              (default: me)

set -eu

REPO="${EMBABEL_REPO:-embabel-worlds/appliance}"
# TODO: default to the latest RELEASE TAG once the appliance cuts them; a branch
# means you get whatever landed this morning, which is not what an installer
# should hand a new user.
REF="${EMBABEL_REF:-main}"
MODE="${EMBABEL_MODE:-${EMBABEL_DOOR:-me}}"   # EMBABEL_DOOR: the old name, still honoured
# ~/embabel-worlds, not ~/embabel-me: one appliance runs both doors, and naming
# the directory after the door you happened to install through was wrong for
# everyone who then opened the other one. An EXISTING ~/embabel-me is still used
# where it is found — a rename is not worth stranding somebody's data over.
DEFAULT_HOME="$HOME/embabel-worlds"
if [ -z "${EMBABEL_HOME:-}" ] && [ -f "$HOME/embabel-me/setup.py" ]; then
  DEFAULT_HOME="$HOME/embabel-me"
fi
HOME_DIR="${EMBABEL_HOME:-$DEFAULT_HOME}"

say() { printf '  %s\n' "$*"; }
die() { printf '\n  %s\n\n' "$*" >&2; exit 1; }

# The door you are actually installing. The banner said "Embabel Me" whichever
# mode you asked for, so the Worlds install opened by naming the other product.
if [ "$MODE" = "worlds" ]; then
  printf '\n  Embabel Worlds — the world your AI acts in\n'
  printf '  A governed, living knowledge graph of your business, built from the\n'
  printf '  systems you already run. It belongs to you, and it runs here.\n\n'
else
  printf '\n  Embabel Me — your own assistant, on your own machine\n\n'
fi

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

# The repo is PUBLIC, so no credential is needed and none is asked for. EMBABEL_TOKEN
# stays for the one case that still needs it: pointing EMBABEL_REPO at a private fork
# or an internal mirror. An empty token is not sent, so the ordinary path is anonymous.
AUTH=""
[ -n "${EMBABEL_TOKEN:-}" ] && AUTH="Authorization: Bearer ${EMBABEL_TOKEN}"

if [ -n "$AUTH" ]; then
  curl -fsSL -H "$AUTH" -o "$TARBALL" "https://api.github.com/repos/$REPO/tarball/$REF" \
    || die "Download failed. Check EMBABEL_TOKEN, your connection, and that '$REF' exists in $REPO."
else
  curl -fsSL -o "$TARBALL" "https://api.github.com/repos/$REPO/tarball/$REF" \
    || die "Download failed. Check your connection, and that '$REF' exists in $REPO."
fi

tar xzf "$TARBALL" -C "$HOME_DIR" --strip-components=1 || die "Could not unpack the download."

# Post-condition, because a download that returns the wrong thing is worse than
# one that fails: prove the install is actually here before promising setup.
[ -f "$HOME_DIR/setup.py" ] || die "The download did not contain an Embabel appliance. Nothing was installed."

chmod +x "$HOME_DIR/me.py" "$HOME_DIR/worlds.py" "$HOME_DIR/setup.py" "$HOME_DIR/embabel" 2>/dev/null || true

# --- 2b. Put `embabel` on PATH --------------------------------------------
# So that everything after this is a verb rather than a directory. Without it the
# instructions are `cd ~/embabel-worlds && ./worlds.py`, and the user has to remember
# where the product lives and which compose file today's mode uses.
#
# A two-line forwarder, not a copy: it execs the checkout's own CLI, so an update
# to this directory updates the command, and there is no second version to drift.
BIN_DIR="${EMBABEL_BIN_DIR:-$HOME/.local/bin}"
if mkdir -p "$BIN_DIR" 2>/dev/null; then
  cat > "$BIN_DIR/embabel" <<SHIM
#!/bin/sh
# Forwards to the Embabel appliance in $HOME_DIR. Written by install.sh.
exec python3 "$HOME_DIR/embabel" "\$@"
SHIM
  chmod +x "$BIN_DIR/embabel"
  case ":$PATH:" in
    *":$BIN_DIR:"*) say "Installed the 'embabel' command to $BIN_DIR." ;;
    *)
      say "Installed the 'embabel' command to $BIN_DIR, which is NOT on your PATH."
      # Name the file THEY use. macOS defaults to zsh and most Linux shells to
      # bash, and telling a bash user to edit ~/.zshrc is telling them nothing.
      case "${SHELL##*/}" in
        zsh)  PROFILE="~/.zshrc" ;;
        bash) PROFILE="~/.bashrc" ;;
        fish) PROFILE="~/.config/fish/config.fish" ;;
        *)    PROFILE="your shell profile" ;;
      esac
      say "Add it to $PROFILE:  export PATH=\"$BIN_DIR:\$PATH\""
      ;;
  esac
  echo
fi

# --- 3. Hand off ----------------------------------------------------------
# setup.py owns the real flow — starting the mode, streaming the first boot,
# the account and model-provider key, and the offer to open the Me app. There
# is deliberately no second implementation of any of that here.
command -v python3 >/dev/null 2>&1 || die "python3 is required (it ships with macOS; on Linux: apt install python3)."

say "Done. Starting setup — after this, use the 'embabel' command."
echo
cd "$HOME_DIR"

# Arguments ride through to the mode script untouched, which is what makes a
# preconfigured install a one-liner:
#   curl -fsSL https://raw.githubusercontent.com/embabel-worlds/appliance/main/install.sh | sh -s -- --world legal-world
# THE WIZARD NEEDS A TERMINAL, and `curl … | sh` has already spent stdin: the
# shell read THIS SCRIPT from that pipe, so by the time setup asks for a username
# stdin is at EOF. It died in a Python traceback on the very first question, so
# every piped install failed in the same place.
#
# Redirect on the hand-off itself rather than `exec < /dev/tty` earlier: this
# script is still being read from that pipe, and a shell that loses its own stdin
# mid-file loses the rest of its script. Only the command that needs the terminal
# gets it.
#
# No terminal (CI, a container, nohup) means no redirect, and setup.py says which
# command to run by hand instead of crashing.
if [ -r /dev/tty ]; then
  if [ "$MODE" = "worlds" ]; then
    exec python3 ./worlds.py "$@" < /dev/tty
  else
    exec python3 ./me.py "$@" < /dev/tty
  fi
fi

if [ "$MODE" = "worlds" ]; then
  exec python3 ./worlds.py "$@"
else
  exec python3 ./me.py "$@"
fi
