#!/bin/sh
# Run install.sh against a FAKE machine and assert on what it refused or did.
#
#   sh scripts/drive-install-sh.sh offline    # no network, no Docker needed
#   sh scripts/drive-install-sh.sh network    # downloads a real tarball
#   sh scripts/drive-install-sh.sh all
#   sh scripts/drive-install-sh.sh no-docker  # one scenario by name
#
# WHY STUBS AND NOT A REAL MACHINE. Every refusal in install.sh describes a
# machine somebody actually had: no docker on PATH, Rancher Desktop on
# containerd, Docker installed but stopped, a directory already holding
# something else. None of those can be produced on a runner that simply has
# Docker working, and all of them are a `command -v` away from being produced
# here. What a real install does is scripts/drive-install.py's job.
#
# WHY A DISTRO MATRIX IN CI. install.sh is #!/bin/sh, so `sh` is dash on Debian,
# busybox ash on Alpine, bash-in-posix-mode on a Mac. The bashisms this script
# must not grow are invisible until one of those runs it, which is why
# .github/workflows/installer.yml runs this file inside four distro images
# rather than four times on ubuntu-latest.
#
# EMBABEL_TEST_REF picks the ref the network scenarios download (default: main).
# CI sets it to the commit under test, so a PR that breaks the tarball fails here
# rather than after the merge.
set -eu

HERE=$(cd "$(dirname "$0")/.." && pwd)
WHICH=${1:-offline}
REF=${EMBABEL_TEST_REF:-main}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

PASSED=0
FAILED=0
SKIPPED=0

# A PATH with the real tools and NOTHING ELSE, so each scenario adds back only
# what it is about.
#
# DISCOVERED, NOT HARD-CODED. This was `/usr/local/bin:/usr/bin:/bin` and left
# Git Bash — where curl lives in /mingw64/bin — with no curl at all, so every
# network scenario failed as "Could not reach GitHub" and blamed the installer
# for the harness. The tools below are the ones install.sh actually calls.
MIN_PATH="$WORK/stub"
for tool in curl tar mktemp sed grep cat chmod sleep uname head dirname basename; do
  path=$(command -v "$tool" 2>/dev/null) || continue
  dir=$(dirname "$path")
  case ":$MIN_PATH:" in *":$dir:"*) ;; *) MIN_PATH="$MIN_PATH:$dir" ;; esac
done

# Whether a REAL docker is unavoidable in those directories. On Linux the CLI
# often sits in /usr/bin beside curl, and dropping that directory would cost us
# curl — so the two scenarios that need docker absent say they were skipped
# rather than quietly testing nothing. In the distro images of
# .github/workflows/installer.yml there is no docker CLI at all, which is where
# these scenarios are a gate.
REAL_DOCKER=""
IFS=: ; for dir in $MIN_PATH; do
  [ "$dir" = "$WORK/stub" ] && continue
  [ -x "$dir/docker" ] && { REAL_DOCKER="$dir/docker"; break; }
done; unset IFS

stub() { # name, body — a fake executable early on PATH
  mkdir -p "$WORK/stub"
  printf '#!/bin/sh\n%s\n' "$2" > "$WORK/stub/$1"
  chmod +x "$WORK/stub/$1"
}

# The docker stub answers the three questions install.sh asks, each overridable
# so one stub covers "working", "no compose" and "not running". `model status`
# fails by default: no Model Runner is the common case and the one whose notice
# must NOT read as a failed install.
stub_docker() {
  stub docker '
case "${1:-} ${2:-}" in
  "compose version") exit ${STUB_COMPOSE_EXIT:-0} ;;
  "info "*|"info") exit ${STUB_INFO_EXIT:-0} ;;
  "model status") exit ${STUB_MODEL_EXIT:-1} ;;
esac
exit 0'
}

# EVERY candidate name find_python() tries, not just python3: the loop prefers
# python3.13 … python3.10 first, and a runner that has one of those would reach
# the real interpreter and run the real wizard. Logging argv is what lets a
# scenario assert WHICH mode script was handed off to.
stub_python() {
  for name in python3.13 python3.12 python3.11 python3.10 python3 python; do
    stub "$name" '
if [ "${1:-}" = "-c" ]; then exit 0; fi   # the >=3.9 probe, and the shim writer
printf "%s\n" "$*" >> "'"$WORK"'/python.log"
# What was typed for it, when a scenario asks: an older uninstall is answered on stdin.
if [ -n "${STUB_READ_STDIN:-}" ]; then
  IFS= read -r line || line=""
  printf "stdin:%s\n" "$line" >> "'"$WORK"'/python.log"
fi
exit 0'
  done
}

reset() { # a clean fake machine between scenarios
  # ${WORK:?} rather than $WORK: an empty WORK would make these three paths
  # /stub, /home and /python.log, and this line runs `rm -rf` on them.
  rm -rf "${WORK:?}/stub" "${WORK:?}/home" "${WORK:?}/python.log"
  mkdir -p "$WORK/home"
}

run() { # runs install.sh with any arguments, captures both streams and the status
  set +e
  # NO_HOME_VAR leaves EMBABEL_HOME unset, which is how an uninstall finds an
  # install by the launcher's record instead of by being told where it is.
  if [ -n "${NO_HOME_VAR:-}" ]; then home_var=""; else home_var="EMBABEL_HOME=${EMBABEL_HOME:-$WORK/home/embabel/worlds}"; fi
  # stdin from /dev/null, not the harness's own: a scenario must never be able to
  # read the terminal the harness was started from.
  env -i \
    PATH="$MIN_PATH" HOME="$WORK/home" \
    ${home_var:+"$home_var"} \
    EMBABEL_REF="$REF" \
    ${EMBABEL_MODE:+EMBABEL_MODE="$EMBABEL_MODE"} \
    ${STUB_COMPOSE_EXIT:+STUB_COMPOSE_EXIT="$STUB_COMPOSE_EXIT"} \
    ${STUB_INFO_EXIT:+STUB_INFO_EXIT="$STUB_INFO_EXIT"} \
    ${STUB_READ_STDIN:+STUB_READ_STDIN="$STUB_READ_STDIN"} \
    sh "$HERE/install.sh" "$@" > "$WORK/out" 2>&1 < /dev/null
  STATUS=$?
  set -e
}

# A file that must come out of a scenario byte for byte as it went in.
same() { # scenario, path, expected content
  if [ "$(cat "$2" 2>/dev/null)" != "$3" ]; then
    FAILED=$((FAILED + 1))
    printf '  x  %s — %s was changed or removed\n' "$1" "$2"
  fi
}

# An install that already exists, as far as install.sh can tell: a setup.py. NEW
# carries the flag a current uninstall understands; an older one does not.
fake_install() { # directory, new|old
  mkdir -p "$1"
  if [ "$2" = new ]; then
    printf '# parser.add_argument("--keep-images")\n' > "$1/setup.py"
  else
    printf '# an uninstall from before --yes\n' > "$1/setup.py"
  fi
}

# The thing that started all this: an unrelated program called `embabel`,
# first on PATH. Its body is what proves it was never touched.
FOREIGN_BODY='#!/bin/sh
echo "an older, unrelated embabel"'
foreign_embabel() {
  stub embabel 'echo "an older, unrelated embabel"'
}

check() { # scenario, expected status, expected substrings…
  scenario=$1; want=$2; shift 2
  bad=""
  [ "$STATUS" = "$want" ] || bad="exited $STATUS, wanted $want"
  for needle in "$@"; do
    grep -F -q -e "$needle" "$WORK/out" || bad="$bad${bad:+; }missing: $needle"
  done
  if [ -n "$bad" ]; then
    FAILED=$((FAILED + 1))
    printf '  x  %s — %s\n' "$scenario" "$bad"
    sed 's/^/       | /' "$WORK/out" | tail -12
  else
    PASSED=$((PASSED + 1))
    printf '  ok %s\n' "$scenario"
  fi
}

wants() { # is this scenario in scope?
  case "$WHICH" in
    all) return 0 ;;
    offline) [ "$2" = offline ] ;;
    network) [ "$2" = network ] ;;
    *) [ "$WHICH" = "$1" ] ;;
  esac
}

# ── no Docker at all ────────────────────────────────────────────────────────
# The essay, not just the verdict: the whole argument of copy/docker-required.txt
# is that a stranger deciding whether this product is worth the trouble gets the
# reason. A refusal that shrinks back to one line is a regression.
if wants no-docker offline && [ -n "$REAL_DOCKER" ]; then
  SKIPPED=$((SKIPPED + 1))
  printf '  -- no-docker (skipped: a real docker at %s cannot be hidden)\n' "$REAL_DOCKER"
elif wants no-docker offline; then
  reset
  run
  check "no-docker" 1 \
    "No 'docker' on your PATH." \
    "Embabel needs Docker, and it is the only thing you have to install yourself."
fi

# ── Rancher Desktop on containerd ───────────────────────────────────────────
# BOTH tools present is the test install.sh makes, and the point is that this
# person gets the one command that fixes it instead of the install-Docker essay.
if wants rancher offline && [ -n "$REAL_DOCKER" ]; then
  SKIPPED=$((SKIPPED + 1))
  printf '  -- rancher-containerd (skipped: a real docker at %s cannot be hidden)\n' "$REAL_DOCKER"
elif wants rancher offline; then
  reset
  stub rdctl 'exit 0'
  stub nerdctl 'exit 0'
  run
  check "rancher-containerd" 1 \
    "Rancher Desktop is set to containerd." \
    "rdctl set --container-engine docker"
  if grep -F -q "the only thing you have to install yourself" "$WORK/out"; then
    FAILED=$((FAILED + 1))
    printf '  x  rancher-containerd — printed the install-Docker essay at somebody who has a runtime\n'
  fi
fi

# ── Docker there, Compose v1 or absent ──────────────────────────────────────
if wants compose-missing offline; then
  reset
  stub_docker
  STUB_COMPOSE_EXIT=1 run
  unset STUB_COMPOSE_EXIT
  check "compose-missing" 1 "Docker Compose v2 is required"
fi

# ── Docker installed but not started ────────────────────────────────────────
# A DIFFERENT problem from missing, and the long essay would be condescending
# here: this person already chose Docker.
if wants not-running offline; then
  reset
  stub_docker
  STUB_INFO_EXIT=1 run
  unset STUB_INFO_EXIT
  check "not-running" 1 "Docker is installed but not running."
fi

# ── the target directory holds somebody else's files ────────────────────────
if wants occupied offline; then
  reset
  stub_docker
  mkdir -p "$WORK/home/occupied"
  : > "$WORK/home/occupied/notes.txt"
  EMBABEL_HOME="$WORK/home/occupied" run
  unset EMBABEL_HOME
  check "occupied-dir" 1 "exists and is not an Embabel install."
  [ -f "$WORK/home/occupied/notes.txt" ] || {
    FAILED=$((FAILED + 1))
    printf '  x  occupied-dir — it refused AND took the files with it\n'
  }
fi

# ── a ref that does not exist ───────────────────────────────────────────────
# 404 is the one status install.sh must not retry, and the message has to name
# the two variables that can be wrong.
if wants bad-ref network; then
  reset
  stub_docker
  # Set and put back by hand, not as `REF=… run`: an assignment before a FUNCTION
  # call outlives it in bash's sh mode (macOS /bin/sh), and every scenario after
  # this one then downloaded a ref that does not exist.
  real_ref=$REF
  REF="no-such-ref-$$"
  run
  REF=$real_ref
  check "bad-ref" 1 "check EMBABEL_REF"
fi

# ── the whole way through, twice ────────────────────────────────────────────
# What this pins is the hand-off: the tarball unpacked, setup.py present, and
# the mode script that was actually launched. EMBABEL_MODE is the variable most
# likely to be read once and then forgotten on one of the two platforms.
if wants happy network; then
  for mode in worlds me; do
    reset
    stub_docker
    stub_python
    EMBABEL_MODE=$mode run
    unset EMBABEL_MODE
    check "happy-$mode" 0 "Done. Starting setup"
    [ -f "$WORK/home/embabel/worlds/setup.py" ] || {
      FAILED=$((FAILED + 1))
      printf '  x  happy-%s — no setup.py after a successful install\n' "$mode"
    }
    grep -F -q "./$mode.py" "$WORK/python.log" 2>/dev/null || {
      FAILED=$((FAILED + 1))
      printf '  x  happy-%s — handed off to %s\n' "$mode" \
        "$(cat "$WORK/python.log" 2>/dev/null || echo 'nothing')"
    }
  done

  # Re-running over an existing install is the UPDATE path, and it says so
  # rather than pretending this is a first install.
  stub_docker
  stub_python
  run
  check "happy-rerun" 0 "Updating" "your .env and data are untouched"
fi

# ── uninstall from the one-liner ────────────────────────────────────────────
# `curl … | sh -s -- --uninstall` finds the install that is THERE and runs its
# own uninstall. It must download nothing — a curl that runs at all fails the
# scenario — and an unrelated `embabel` on PATH must come out untouched.
if wants uninstall offline; then
  reset
  stub_python
  foreign_embabel
  stub curl 'echo downloaded >> "'"$WORK"'/curl.log"; exit 1'
  rm -f "$WORK/curl.log"
  fake_install "$WORK/home/embabel/worlds" new
  run --uninstall --yes
  check "uninstall" 0 "Found Embabel in"
  grep -F -q "$WORK/home/embabel/worlds/setup.py --uninstall --yes" "$WORK/python.log" 2>/dev/null || {
    FAILED=$((FAILED + 1))
    printf '  x  uninstall — ran %s\n' "$(cat "$WORK/python.log" 2>/dev/null || echo 'nothing')"
  }
  [ ! -f "$WORK/curl.log" ] || {
    FAILED=$((FAILED + 1))
    printf '  x  uninstall — it downloaded something on the way to removing\n'
  }
  same "uninstall" "$WORK/stub/embabel" "$FOREIGN_BODY"
fi

# Nothing installed is not an error: the person wanted it gone, and it is.
if wants uninstall-none offline; then
  reset
  stub_python
  run --uninstall --yes
  check "uninstall-none" 0 "No Embabel install found"
  [ ! -f "$WORK/python.log" ] || {
    FAILED=$((FAILED + 1))
    printf '  x  uninstall-none — ran setup with nothing to uninstall\n'
  }
fi

# EMBABEL_HOME not given and nothing in the default place: the launcher records
# where it forwards to, and that is the install to remove.
if wants uninstall-recorded offline; then
  reset
  stub_python
  fake_install "$WORK/home/elsewhere" new
  mkdir -p "$WORK/home/.local/bin"
  printf '#!/bin/sh\n# Forwards to the Embabel appliance in %s. Written by install.sh.\n' \
    "$WORK/home/elsewhere" > "$WORK/home/.local/bin/embabel"
  NO_HOME_VAR=1 run --uninstall --yes
  check "uninstall-recorded" 0 "Found Embabel in $WORK/home/elsewhere"
fi

# An install older than --yes is answered on stdin instead — with the "yes" its
# own question wants, which is what --yes means.
if wants uninstall-old offline; then
  reset
  stub_python
  fake_install "$WORK/home/embabel/worlds" old
  STUB_READ_STDIN=1 run --uninstall --yes
  check "uninstall-old" 0 "Found Embabel in"
  grep -F -q "stdin:yes" "$WORK/python.log" 2>/dev/null || {
    FAILED=$((FAILED + 1))
    printf '  x  uninstall-old — the older uninstall was not answered: %s\n' \
      "$(cat "$WORK/python.log" 2>/dev/null || echo 'nothing ran')"
  }
fi

# No terminal and no --yes: refuse, and say how to answer in advance — never
# take silence for a yes. Only where there is genuinely no terminal to open.
if wants uninstall-no-tty offline && ( : < /dev/tty ) 2>/dev/null; then
  SKIPPED=$((SKIPPED + 1))
  printf '  -- uninstall-no-tty (skipped: this harness has a terminal)\n'
elif wants uninstall-no-tty offline; then
  reset
  stub_python
  fake_install "$WORK/home/embabel/worlds" new
  run --uninstall
  check "uninstall-no-tty" 1 "No terminal to ask on" "--uninstall --yes"
  [ ! -f "$WORK/python.log" ] || {
    FAILED=$((FAILED + 1))
    printf '  x  uninstall-no-tty — ran the uninstall without anybody saying yes\n'
  }
fi

# ── a foreign `embabel` on PATH during install ──────────────────────────────
# The installer leaves it alone, and then must say so — with this appliance's
# full path — instead of "use the 'embabel' command", which would run the other.
if wants foreign network; then
  reset
  stub_docker
  stub_python
  foreign_embabel
  run
  check "foreign-embabel" 0 "Done. Starting setup" \
    'Another program called "embabel" is on your PATH' \
    "$WORK/home/embabel/worlds/embabel uninstall"
  if grep -F -q "after this, use the 'embabel' command" "$WORK/out"; then
    FAILED=$((FAILED + 1))
    printf "  x  foreign-embabel — still told them to use the 'embabel' command\n"
  fi
  same "foreign-embabel" "$WORK/stub/embabel" "$FOREIGN_BODY"
fi

printf '\n  %d passed, %d failed' "$PASSED" "$FAILED"
[ "$SKIPPED" = 0 ] && printf '\n' || printf ', %d skipped\n' "$SKIPPED"
[ "$FAILED" = 0 ] || exit 1
